import "server-only";
import { resolve4 } from "node:dns/promises";
import { get as httpGet } from "node:http";
import { get as httpsGet } from "node:https";
import { BlockList, isIP } from "node:net";

const blocked = new BlockList();
for (const [address, prefix] of [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.2.0", 24],
  ["192.88.99.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 3],
] as const)
  blocked.addSubnet(address, prefix, "ipv4");

const maxBytes = 2 * 1024 * 1024;

function imageType(bytes: Buffer): string | null {
  if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
    return "image/png";
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
  if (["GIF87a", "GIF89a"].includes(bytes.toString("ascii", 0, 6))) return "image/gif";
  if (bytes.toString("ascii", 0, 4) === "RIFF" && bytes.toString("ascii", 8, 12) === "WEBP")
    return "image/webp";
  return null;
}

async function download(url: URL, signal: AbortSignal, redirects = 0): Promise<string> {
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    (url.port && !["80", "443"].includes(url.port))
  )
    throw new Error("Invalid avatar URL");
  // Use public IPv4 addresses only, pinning the connection to the validated DNS answer.
  const addresses = isIP(url.hostname) ? [url.hostname] : await resolve4(url.hostname);
  signal.throwIfAborted();
  if (
    !addresses.length ||
    addresses.some((address) => isIP(address) !== 4 || blocked.check(address, "ipv4"))
  ) {
    throw new Error("Avatar host is not public");
  }
  return new Promise((resolve, reject) => {
    const get = url.protocol === "https:" ? httpsGet : httpGet;
    const request = get(
      url,
      {
        hostname: addresses[0],
        servername: url.hostname,
        headers: { Host: url.host, Accept: "image/png,image/jpeg,image/webp,image/gif" },
        agent: false,
        signal,
      },
      (response) => {
        if ([301, 302, 303, 307, 308].includes(response.statusCode ?? 0)) {
          response.destroy();
          if (!response.headers.location || redirects >= 3)
            return reject(new Error("Avatar redirect limit"));
          try {
            resolve(download(new URL(response.headers.location, url), signal, redirects + 1));
          } catch (error) {
            reject(error);
          }
          return;
        }
        if (response.statusCode !== 200 || Number(response.headers["content-length"]) > maxBytes) {
          response.destroy();
          reject(new Error("Avatar unavailable"));
          return;
        }
        const chunks: Buffer[] = [];
        let size = 0;
        response.on("data", (chunk: Buffer) => {
          size += chunk.length;
          if (size > maxBytes) response.destroy(new Error("Avatar too large"));
          else chunks.push(chunk);
        });
        response.on("error", reject);
        response.on("end", () => {
          const bytes = Buffer.concat(chunks);
          const type = imageType(bytes);
          if (!type) reject(new Error("Unsupported avatar image"));
          else resolve(`data:${type};base64,${bytes.toString("base64")}`);
        });
      },
    );
    request.on("error", reject);
  });
}

export async function cardAvatar(url: string | null | undefined): Promise<string | null> {
  if (!url) return null;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  try {
    return await Promise.race([
      download(new URL(url), controller.signal),
      new Promise<null>((resolve) => {
        timer = setTimeout(() => {
          controller.abort();
          resolve(null);
        }, 5000);
      }),
    ]);
  } catch {
    return null;
  } finally {
    clearTimeout(timer!);
  }
}

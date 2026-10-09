import "server-only";
import sharp from "sharp";
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
const maxCachedBytes = 8 * 1024 * 1024;
const maxCachedImages = 96;
const imageCacheTtl = 30 * 60 * 1000;
const failureCacheTtl = 15 * 1000;
const imageCache = new Map<string, { value: string | null; expiresAt: number; bytes: number }>();
const imageRequests = new Map<string, Promise<string | null>>();
let cachedBytes = 0;

function cacheKey(url: string, kind: "avatar" | "logo", size = 256) {
  return `${kind}:${url}${size === 256 ? "" : `:${size}`}`;
}

function readCached(key: string): { hit: boolean; value: string | null } {
  const entry = imageCache.get(key);
  if (!entry) return { hit: false, value: null };
  if (entry.expiresAt <= Date.now()) {
    imageCache.delete(key);
    cachedBytes -= entry.bytes;
    return { hit: false, value: null };
  }
  imageCache.delete(key);
  imageCache.set(key, entry);
  return { hit: true, value: entry.value };
}

function writeCached(key: string, value: string | null) {
  const bytes = value ? Buffer.byteLength(value) : 0;
  const ttl = value ? imageCacheTtl : failureCacheTtl;
  const previous = imageCache.get(key);
  if (previous) cachedBytes -= previous.bytes;
  imageCache.delete(key);
  imageCache.set(key, { value, expiresAt: Date.now() + ttl, bytes });
  cachedBytes += bytes;
  while (imageCache.size > maxCachedImages || cachedBytes > maxCachedBytes) {
    const oldest = imageCache.keys().next().value;
    if (!oldest) break;
    const entry = imageCache.get(oldest);
    imageCache.delete(oldest);
    cachedBytes -= entry?.bytes ?? 0;
  }
}

/** Return a previously sanitized image without waiting for network or image processing. */
export function cachedCardImage(url: string | null | undefined, kind: "avatar" | "logo") {
  if (!url) return null;
  return readCached(cacheKey(url, kind)).value;
}

function imageType(bytes: Buffer): string | null {
  if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
    return "image/png";
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
  if (["GIF87a", "GIF89a"].includes(bytes.toString("ascii", 0, 6))) return "image/gif";
  if (bytes.toString("ascii", 0, 4) === "RIFF" && bytes.toString("ascii", 8, 12) === "WEBP")
    return "image/webp";
  return null;
}

async function download(
  url: URL,
  signal: AbortSignal,
  kind: "avatar" | "logo",
  redirects = 0,
  avatarSize = 256,
): Promise<string> {
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
        headers: {
          Host: url.host,
          Accept: "image/png,image/jpeg,image/webp,image/gif",
        },
        agent: false,
        signal,
      },
      (response) => {
        if ([301, 302, 303, 307, 308].includes(response.statusCode ?? 0)) {
          response.destroy();
          if (!response.headers.location || redirects >= 3)
            return reject(new Error("Avatar redirect limit"));
          try {
            resolve(
              download(
                new URL(response.headers.location, url),
                signal,
                kind,
                redirects + 1,
                avatarSize,
              ),
            );
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
          const savedSvg =
            url.pathname.startsWith("/originals/topic-logos/") &&
            response.headers["content-type"]?.split(";")[0] === "image/svg+xml";
          if (kind === "logo" && (type === "image/webp" || type === "image/png" || savedSvg)) {
            resolve(managedLogo(bytes, savedSvg ? "image/svg+xml" : type!));
          } else if (kind === "avatar" && type) {
            resolve(rasterAvatar(bytes, avatarSize));
          } else reject(new Error("Unsupported card image"));
        });
      },
    );
    request.on("error", reject);
  });
}

/** Validate the finished asset without resizing or recompressing it. */
async function managedLogo(bytes: Buffer, mime: string) {
  const metadata = await sharp(bytes, { limitInputPixels: 96 * 96 }).metadata();
  if (!metadata.width || !metadata.height || metadata.width > 96 || metadata.height > 96)
    throw new Error("Invalid managed logo dimensions");
  if (metadata.format !== (mime === "image/svg+xml" ? "svg" : mime.slice(6)))
    throw new Error("Invalid managed logo format");
  return `data:${mime};base64,${bytes.toString("base64")}`;
}

async function rasterAvatar(bytes: Buffer, size = 256) {
  const image = await sharp(bytes, { limitInputPixels: 4_000_000 })
    .resize(size, size, { fit: "cover" })
    .webp({ quality: 80 })
    .timeout({ seconds: 2 })
    .toBuffer();
  return `data:image/webp;base64,${image.toString("base64")}`;
}

export async function cardImage(
  url: string | null | undefined,
  kind: "avatar" | "logo",
  timeoutMs = 5000,
  avatarSize = 256,
): Promise<string | null> {
  if (!url || timeoutMs <= 0) return null;
  const key = cacheKey(url, kind, avatarSize);
  const cached = readCached(key);
  if (cached.hit) return cached.value;
  const pending = imageRequests.get(key);
  if (pending) return pending;
  if (imageRequests.size >= 16) return null;
  const request = loadCardImage(url, kind, timeoutMs, avatarSize).then((value) => {
    writeCached(key, value);
    return value;
  });
  imageRequests.set(key, request);
  try {
    return await request;
  } finally {
    imageRequests.delete(key);
  }
}

async function loadCardImage(
  url: string,
  kind: "avatar" | "logo",
  timeoutMs: number,
  avatarSize = 256,
): Promise<string | null> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  try {
    return await Promise.race([
      download(new URL(url), controller.signal, kind, 0, avatarSize),
      new Promise<null>((resolve) => {
        timer = setTimeout(() => {
          controller.abort();
          resolve(null);
        }, timeoutMs);
      }),
    ]);
  } catch {
    return null;
  } finally {
    clearTimeout(timer!);
  }
}

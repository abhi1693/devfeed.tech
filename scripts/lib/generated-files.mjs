import { constants } from "node:fs";
import { open } from "node:fs/promises";

// Generators require a trusted checkout with stable parent directories.
// O_NOFOLLOW constrains the final path component; it cannot prevent parent directory renames.
const noFollow = constants.O_NOFOLLOW | constants.O_NONBLOCK;

async function openFile(path, flags, label) {
  try {
    return await open(path, flags | noFollow);
  } catch (error) {
    if (error.code === "ELOOP")
      throw new Error(`Cannot manage symlinked ${label}`, { cause: error });
    if (error.code === "EISDIR")
      throw new Error(`Cannot manage non-file ${label}`, { cause: error });
    throw error;
  }
}

async function requireFile(handle, label) {
  if (!(await handle.stat()).isFile()) throw new Error(`Cannot manage non-file ${label}`);
}

/** Validate and read the opened file, never a second lookup of its pathname. */
export async function readRegularFile(path, label) {
  const handle = await openFile(path, constants.O_RDONLY, label);
  try {
    await requireFile(handle, label);
    return await handle.readFile("utf8");
  } finally {
    await handle.close();
  }
}

/** Revalidate ownership on the same handle that receives the generated content. */
export async function writeGeneratedFile(path, content, { marker, label }) {
  let handle;
  let created = false;
  for (let attempt = 0; attempt < 3 && !handle; attempt += 1) {
    try {
      handle = await openFile(path, constants.O_RDWR, label);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      try {
        handle = await openFile(
          path,
          constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL,
          label,
        );
        created = true;
      } catch (creationError) {
        if (creationError.code !== "EEXIST") throw creationError;
      }
    }
  }
  if (!handle) throw new Error(`Cannot manage concurrently replaced ${label}`);
  try {
    await requireFile(handle, label);
    if (!created && !(await handle.readFile("utf8")).includes(marker)) {
      throw new Error(`Refusing to overwrite a handwritten ${label}`);
    }
    const buffer = Buffer.from(content);
    let offset = 0;
    while (offset < buffer.length) {
      const { bytesWritten } = await handle.write(buffer, offset, buffer.length - offset, offset);
      if (!bytesWritten) throw new Error(`Could not write generated ${label}`);
      offset += bytesWritten;
    }
    await handle.truncate(buffer.length);
  } finally {
    await handle.close();
  }
}

// Keep browser/Next storage disabled with fetch's no-store option, while allowing
// the API's generation-validated public cache. An explicit header prevents fetch
// from translating no-store into a wire-level no-cache on every reader request.
export const publicReadCacheControl = "max-age=600";

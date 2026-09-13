export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { registerTelemetry } = await import("@devfeed/telemetry/server");
    await registerTelemetry("admin");
  }
}

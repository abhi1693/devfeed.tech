import { NextResponse } from "next/server";

// Reject before layout streaming: a streamed not-found page can otherwise be 200.
export function proxy() {
  return new NextResponse(null, { status: 404 });
}

export const config = { matcher: ["/metrics/:path*"] };

"use client";

import { usePathname } from "next/navigation";
import { SignupNudge } from "./signup-nudge";

export function WebSignupNudge() {
  return <SignupNudge pathname={usePathname() ?? ""} />;
}

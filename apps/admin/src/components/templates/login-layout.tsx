import { Brand } from "@/components/molecules/brand";

export function LoginLayout({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-dvh flex-col items-center justify-center gap-7 px-5 py-12">
    <Brand />{children}
  </main>;
}

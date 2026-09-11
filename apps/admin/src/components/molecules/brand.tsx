import Link from "next/link";
import Image from "next/image";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";

export function Brand() {
  return <Link href="/" aria-label="DevFeed home" className="inline-flex items-center gap-2 rounded-sm focus-visible:outline-2">
    <span className="devfeed-brand">
      <Image className="devfeed-brand-mark" src={brandMark} alt="" width={40} height={40} priority />
      <span>devfeed.</span>
    </span>
  </Link>;
}

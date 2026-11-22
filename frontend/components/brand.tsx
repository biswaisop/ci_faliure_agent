import { Terminal } from "lucide-react";
import Link from "next/link";

export function Brand() {
  return (
    <Link href="/" className="brand">
      <span className="brand-mark"><Terminal aria-hidden="true" size={17} /></span>
      AutopCI
    </Link>
  );
}

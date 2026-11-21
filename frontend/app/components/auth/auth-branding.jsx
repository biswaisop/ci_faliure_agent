import { AudioLines } from "lucide-react";
import Link from "next/link";
import { CiPreview } from "./ci-preview";

export function AuthBrand() {
  return (
    <Link href="/" className="inline-flex w-fit items-center gap-3 text-sm font-semibold tracking-tight">
      <span className="grid size-9 place-items-center rounded-[9px] border border-[#3e3e6c] bg-[#22223b] text-[#a5a7fc]">
        <AudioLines size={19} aria-hidden="true" />
      </span>
      CI Postmortem Agent
    </Link>
  );
}

export function AuthBranding() {
  return (
    <aside className="auth-branding hidden min-w-0 border-r border-[#27272a] px-[clamp(2.25rem,5vw,6rem)] py-9 lg:grid lg:grid-rows-[auto_1fr]">
      <AuthBrand />
      <div className="mx-auto w-full max-w-[590px] self-center py-12">
        <h2 className="text-[clamp(2.65rem,4.15vw,4.4rem)] leading-[1.04] font-semibold tracking-[-0.063em]">
          Every failed build<br />has a story.
          <span className="mt-1 block text-[#aaaafa]">Find the root cause.</span>
        </h2>
        <p className="mt-5 max-w-[44ch] text-[15px] leading-7 text-[#a1a1aa]">
          Stop digging through endless CI logs. Let AI investigate failed workflows and deliver actionable diagnoses.
        </p>
        <CiPreview />
      </div>
    </aside>
  );
}

import type { ComponentProps } from "react";

export function Badge({ className = "", ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="badge"
      className={`inline-flex w-fit shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${className}`}
      {...props}
    />
  );
}

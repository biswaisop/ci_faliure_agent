import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

const buttonVariants = cva(
  "inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-sm border px-4 text-sm font-semibold transition-colors motion-reduce:transition-none disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "border-transparent bg-accent text-[#171d0f] hover:bg-[#e0ffab]",
        outline: "border-line bg-[#171a17] hover:border-accent hover:bg-[#21271c]",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ asChild, className, variant, ...props }: ButtonProps) {
  const Component = asChild ? Slot : "button";

  return <Component data-slot="button" className={buttonVariants({ variant, className })} {...props} />;
}

import type { ComponentProps } from "react";

export function Card({ className = "", ...props }: ComponentProps<"div">) {
  return <div data-slot="card" className={`flex flex-col rounded-xl border ${className}`} {...props} />;
}

export function CardHeader(props: ComponentProps<"div">) {
  return <div data-slot="card-header" {...props} />;
}

export function CardContent(props: ComponentProps<"div">) {
  return <div data-slot="card-content" {...props} />;
}

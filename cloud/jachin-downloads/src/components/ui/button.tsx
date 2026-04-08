import * as React from "react";
import { cn } from "@/lib/utils";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "outline" | "ghost" | "link";
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 disabled:pointer-events-none disabled:opacity-50",
          variant === "default" &&
            "bg-cyan-600/90 text-white hover:bg-cyan-500 px-4 py-2",
          variant === "outline" &&
            "border border-white/15 bg-transparent hover:bg-white/5 px-4 py-2",
          variant === "ghost" && "hover:bg-white/5 px-4 py-2",
          variant === "link" && "text-cyan-400 underline-offset-4 hover:underline",
          className
        )}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button };

import { Slot } from '@radix-ui/react-slot'
import { type VariantProps, cva } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all duration-200 disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70 focus-visible:ring-offset-2 focus-visible:ring-offset-bg active:scale-[0.98]',
  {
    variants: {
      variant: {
        // Frosted-glass neutral button.
        default:
          'border border-white/10 bg-white/5 text-text backdrop-blur hover:border-white/20 hover:bg-white/10',
        outline:
          'border border-white/10 bg-transparent text-text-muted hover:border-accent/50 hover:text-text hover:bg-white/5',
        ghost: 'text-text-muted hover:bg-white/5 hover:text-text',
        // Primary: violet gradient pill with a soft bloom.
        accent:
          'bg-gradient-to-b from-accent-hover to-accent text-text font-semibold shadow-[0_6px_20px_-6px_rgba(124,108,255,0.65)] hover:shadow-[0_8px_28px_-6px_rgba(124,108,255,0.85)]',
        // Attention: azure gradient, dark text for contrast against the bright fill.
        accent2:
          'bg-gradient-to-b from-accent-2-hover to-accent-2 text-bg font-semibold shadow-[0_6px_20px_-6px_rgba(77,201,255,0.55)] hover:shadow-[0_8px_28px_-6px_rgba(77,201,255,0.7)]',
        // Hero CTA: a liquid-glass lens. The paired inset shadows (light on the
        // top edge, dark on the bottom) read as a refractive bevel rather than a
        // flat fill, and the violet bloom ties it to the ambient signal field.
        lens: 'bg-gradient-to-b from-white to-[#d9dcf2] text-bg font-semibold shadow-[inset_0_1px_0_0_rgba(255,255,255,0.75),inset_0_-1px_0_0_rgba(0,0,0,0.28),0_10px_34px_-12px_rgba(124,108,255,0.8)] hover:from-white hover:to-white hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.9),inset_0_-1px_0_0_rgba(0,0,0,0.22),0_14px_44px_-12px_rgba(124,108,255,0.95)]',
      },
      size: {
        sm: 'px-3 py-1.5 text-xs',
        md: 'px-4 py-2 text-sm',
        lg: 'px-5 py-2.5 text-sm',
      },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />
}

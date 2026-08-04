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
        // Primary: AMD-red gradient pill with a soft glow.
        accent:
          'bg-gradient-to-b from-accent to-red-700 text-text font-semibold shadow-[0_6px_20px_-6px_rgba(226,58,58,0.6)] hover:from-accent-hover hover:to-accent hover:shadow-[0_8px_28px_-6px_rgba(226,58,58,0.75)]',
        // Attention: warm gold gradient.
        accent2:
          'bg-gradient-to-b from-accent-2 to-amber-600 text-bg font-semibold shadow-[0_6px_20px_-6px_rgba(212,175,55,0.55)] hover:from-accent-2-hover hover:to-accent-2',
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

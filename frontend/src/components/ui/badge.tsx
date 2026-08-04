import { type VariantProps, cva } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

// Soft "glass pill" badges: a low-opacity tint of the signal color with matching
// ink and a hairline ring, instead of a solid saturated block. Reads as a lit
// status chip on the frosted surfaces and keeps status legible on dark glass.
const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
  {
    variants: {
      variant: {
        neutral: 'bg-white/5 text-text-muted ring-white/10',
        accent: 'bg-accent/12 text-accent ring-accent/30',
        accent2: 'bg-accent-2/12 text-accent-2 ring-accent-2/30',
        healthy: 'bg-healthy/15 text-healthy ring-healthy/30',
        degrading: 'bg-degrading/15 text-degrading ring-degrading/30',
        faulty: 'bg-faulty/15 text-faulty ring-faulty/30',
        critical: 'bg-critical/15 text-critical ring-critical/30',
        unknown: 'bg-unknown/15 text-unknown ring-unknown/30',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

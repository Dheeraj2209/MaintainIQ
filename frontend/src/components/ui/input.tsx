import type { InputHTMLAttributes, LabelHTMLAttributes, SelectHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const fieldClasses =
  'rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-text backdrop-blur transition placeholder:text-text-muted/60 focus:border-accent/60 focus:bg-white/[0.07] focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-50'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(fieldClasses, className)} {...props} />
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(fieldClasses, className)} {...props} />
}

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn('grid gap-1.5 text-xs font-medium text-text-muted', className)} {...props} />
}

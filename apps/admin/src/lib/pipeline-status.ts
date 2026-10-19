import {
  CheckCheck,
  CircleAlert,
  CircleCheck,
  CircleX,
  Clock3,
  LoaderCircle,
  TimerReset,
} from "lucide-react";

export const pipelineCardStatuses = {
  waiting: {
    label: "Waiting",
    icon: Clock3,
    color: "text-amber-700 dark:text-amber-300",
    card: "border-amber-200 border-l-amber-400 bg-amber-50/70 dark:border-amber-900 dark:border-l-amber-500 dark:bg-amber-950/25",
  },
  running: {
    label: "Running",
    icon: LoaderCircle,
    color: "text-blue-700 dark:text-blue-300",
    card: "border-blue-200 border-l-blue-400 bg-blue-50/70 dark:border-blue-900 dark:border-l-blue-500 dark:bg-blue-950/25",
  },
  retry_wait: {
    label: "Waiting to retry",
    icon: TimerReset,
    color: "text-violet-700 dark:text-violet-300",
    card: "border-violet-200 border-l-violet-400 bg-violet-50/70 dark:border-violet-900 dark:border-l-violet-500 dark:bg-violet-950/25",
  },
  blocked: {
    label: "Blocked",
    icon: CircleAlert,
    color: "text-rose-700 dark:text-rose-300",
    card: "border-rose-200 border-l-rose-400 bg-rose-50/70 dark:border-rose-900 dark:border-l-rose-500 dark:bg-rose-950/25",
  },
  ready: {
    label: "Ready",
    icon: CircleCheck,
    color: "text-emerald-700 dark:text-emerald-300",
    card: "border-emerald-200 border-l-emerald-400 bg-emerald-50/70 dark:border-emerald-900 dark:border-l-emerald-500 dark:bg-emerald-950/25",
  },
  complete: {
    label: "Complete",
    icon: CheckCheck,
    color: "text-slate-600 dark:text-slate-300",
    card: "border-slate-200 border-l-slate-400 bg-slate-50/70 dark:border-slate-800 dark:border-l-slate-500 dark:bg-slate-950/25",
  },
  published: {
    label: "Published",
    icon: CheckCheck,
    color: "text-green-700 dark:text-green-300",
    card: "border-green-200 border-l-green-400 bg-green-50/70 dark:border-green-900 dark:border-l-green-500 dark:bg-green-950/25",
  },
  rejected: {
    label: "Rejected",
    icon: CircleX,
    color: "text-rose-700 dark:text-rose-300",
    card: "border-rose-200 border-l-rose-400 bg-rose-50/70 dark:border-rose-900 dark:border-l-rose-500 dark:bg-rose-950/25",
  },
};

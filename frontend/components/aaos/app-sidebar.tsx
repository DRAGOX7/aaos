import Link from "next/link";
import {
  Activity,
  BookOpen,
  CheckCircle2,
  ClipboardList,
  FileText,
  HeartPulse,
  LayoutDashboard,
  ListTodo,
  MessageCircle,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

type NavigationItem = Readonly<{
  label: string;
  href: string;
  icon: LucideIcon;
}>;

export const AAOS_NAVIGATION: readonly NavigationItem[] = [
  {
    label: "Overview",
    href: "/",
    icon: LayoutDashboard,
  },
  {
    label: "Courses",
    href: "/courses",
    icon: BookOpen,
  },
  {
    label: "Assignments",
    href: "/assignments",
    icon: ClipboardList,
  },
  {
    label: "Documents",
    href: "/documents",
    icon: FileText,
  },
  {
    label: "Ask AAOS",
    href: "/ask",
    icon: MessageCircle,
  },
  {
    label: "Tasks",
    href: "/tasks",
    icon: ListTodo,
  },
  {
    label: "Approvals",
    href: "/approvals",
    icon: CheckCircle2,
  },
  {
    label: "Activity",
    href: "/activity",
    icon: Activity,
  },
  {
    label: "System Health",
    href: "/system-health",
    icon: HeartPulse,
  },
];

type AppSidebarProps = Readonly<{
  className?: string;
  activeHref?: string;
}>;

export function AppSidebar({
  className,
  activeHref = "/",
}: AppSidebarProps) {
  return (
    <aside
      className={cn(
        "flex h-full w-64 flex-col border-r border-sidebar-border bg-white/88 shadow-[8px_0_32px_rgb(28_52_91_/_0.035)] backdrop-blur-xl",
        className,
      )}
    >
      <div className="flex items-center gap-3 px-6 pb-6 pt-7">
        <div
          aria-hidden="true"
          className="flex size-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-[0_8px_18px_rgb(22_119_242_/_0.22)]"
        >
          <LayoutDashboard className="size-5" strokeWidth={2} />
        </div>

        <div className="min-w-0">
          <p className="text-base font-semibold tracking-tight text-sidebar-foreground">
            AAOS
          </p>
          <p className="text-xs text-muted-foreground">
            Academic AI OS
          </p>
        </div>
      </div>

      <nav
        aria-label="AAOS"
        className="flex flex-1 flex-col gap-1 px-3"
      >
        {AAOS_NAVIGATION.map((item) => {
          const Icon = item.icon;
          const active = item.href === activeHref;

          return (
            <Link
              key={item.label}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-all duration-200",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm before:absolute before:-left-3 before:h-6 before:w-1 before:rounded-r-full before:bg-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon
                aria-hidden="true"
                className="size-[18px] shrink-0"
                strokeWidth={1.9}
              />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mx-4 mb-5 mt-6 rounded-2xl border border-border bg-primary-soft/60 px-4 py-3">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">
          AAOS workspace
        </p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          Trusted academic context, kept close at hand.
        </p>
      </div>
    </aside>
  );
}

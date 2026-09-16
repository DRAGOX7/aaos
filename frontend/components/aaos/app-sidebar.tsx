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
  Settings,
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
  {
    label: "Settings",
    href: "/settings",
    icon: Settings,
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
        "flex h-full w-64 flex-col border-r border-sidebar-border bg-sidebar",
        className,
      )}
    >
      <div className="flex items-center gap-3 px-6 pb-6 pt-7">
        <div
          aria-hidden="true"
          className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm"
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
                "flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
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
    </aside>
  );
}

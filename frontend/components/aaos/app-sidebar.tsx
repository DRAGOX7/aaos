"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
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

import { AaosMark } from "@/components/aaos/aaos-mark";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type NavigationItem = Readonly<{
  label: MessageKey;
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

export function resolveActiveNavigationHref(pathname: string): string {
  const match = AAOS_NAVIGATION
    .filter((item) => item.href === "/"
      ? pathname === "/"
      : pathname === item.href || pathname.startsWith(`${item.href}/`))
    .sort((left, right) => right.href.length - left.href.length)[0];

  return match?.href ?? "/";
}

type AppSidebarProps = Readonly<{
  className?: string;
  activeHref?: string;
}>;

export function AppSidebar({
  className,
  activeHref,
}: AppSidebarProps) {
  // usePathname follows client-side navigation; a popstate listener missed
  // Link and router.push moves and left the previous page highlighted.
  const pathname = usePathname() ?? "/";
  const { t } = useI18n();

  const resolvedActiveHref = activeHref ?? resolveActiveNavigationHref(pathname);

  return (
    <aside
      className={cn(
        "flex h-full w-64 flex-col border-e border-sidebar-border bg-card",
        className,
      )}
    >
      <div className="flex items-center gap-3 px-6 pb-6 pt-7">
        <AaosMark className="size-10 shrink-0" />

        <div className="min-w-0">
          <p className="text-[17px] font-extrabold tracking-[-0.01em] text-sidebar-foreground">
            AAOS
          </p>
          <p className="text-xs text-muted-foreground">
            {t("Academic AI OS")}
          </p>
        </div>
      </div>

      <nav
        aria-label="AAOS"
        className="flex flex-1 flex-col gap-1 px-3"
      >
        {AAOS_NAVIGATION.map((item) => {
          const Icon = item.icon;
          const active = item.href === resolvedActiveHref;

          return (
            <Link
              key={item.label}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-all duration-200",
                active
                  ? "bg-primary-soft font-semibold text-foreground [&>svg]:text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon
                aria-hidden="true"
                className="size-[18px] shrink-0"
                strokeWidth={1.9}
              />
              <span>{t(item.label)}</span>
            </Link>
          );
        })}
      </nav>

    </aside>
  );
}

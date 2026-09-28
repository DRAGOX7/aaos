import type { ReactNode } from "react";

import { AppSidebar } from "@/components/aaos/app-sidebar";
import { ShellPageHeader } from "@/components/aaos/shell-page-header";

type AppShellProps = Readonly<{
  children: ReactNode;
}>;

export function AppShell({
  children,
}: AppShellProps) {
  return (
    <div className="min-h-[100dvh] bg-transparent lg:flex">
      <div className="hidden shrink-0 lg:block">
        <AppSidebar className="sticky top-0 min-h-[100dvh]" />
      </div>

      <div className="min-w-0 flex-1">
        <ShellPageHeader />

        <main className="min-h-[calc(100dvh-5rem)] px-5 py-7 sm:px-6 lg:px-8 lg:py-9">
          {children}
        </main>
      </div>
    </div>
  );
}

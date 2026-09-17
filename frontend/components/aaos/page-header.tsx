import type { ReactNode } from "react";

type PageHeaderProps = Readonly<{
  title?: string;
  description?: string;
  mobileNavigation?: ReactNode;
}>;

export function PageHeader({
  title = "Overview",
  description = "Your academic workspace",
  mobileNavigation,
}: PageHeaderProps) {
  return (
    <header className="border-b border-border bg-card">
      <div className="flex min-h-20 items-center gap-3 px-5 sm:px-6 lg:px-8">
        {mobileNavigation ? (
          <div className="lg:hidden">
            {mobileNavigation}
          </div>
        ) : null}

        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
            {title}
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {description}
          </p>
        </div>
      </div>
    </header>
  );
}

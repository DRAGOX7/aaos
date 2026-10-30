"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";

import { AaosMark } from "@/components/aaos/aaos-mark";
import {
  AAOS_NAVIGATION,
  resolveActiveNavigationHref,
} from "@/components/aaos/app-sidebar";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function MobileNav() {
  const { t, dir } = useI18n();
  const activeHref = resolveActiveNavigationHref(usePathname());
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("Open navigation")}
        >
          <Menu aria-hidden="true" className="size-5" />
        </Button>
      </SheetTrigger>

      <SheetContent
        side={dir === "rtl" ? "right" : "left"}
        className="w-[18rem] max-w-[88vw] gap-0 p-0"
      >
        <SheetTitle className="sr-only">
          {t("AAOS navigation")}
        </SheetTitle>

        <div className="flex items-center gap-3 px-6 pb-6 pt-7">
          <AaosMark className="size-10 shrink-0" />

          <div>
            <p className="text-base font-semibold tracking-tight">
              AAOS
            </p>
            <p className="text-xs text-muted-foreground">
              {t("Academic AI OS")}
            </p>
          </div>
        </div>

        <nav
          aria-label="AAOS mobile"
          className="flex flex-col gap-1 px-3"
        >
          {AAOS_NAVIGATION.map((item) => {
            const Icon = item.icon;
            const active = item.href === activeHref;

            return (
              <SheetClose key={item.label} asChild>
                <Link
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
                  <span>{t(item.label)}</span>
                </Link>
              </SheetClose>
            );
          })}
        </nav>
      </SheetContent>
    </Sheet>
  );
}

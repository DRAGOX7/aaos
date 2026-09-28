"use client";

import { usePathname } from "next/navigation";

import { MobileNav } from "@/components/aaos/mobile-nav";
import { PageHeader } from "@/components/aaos/page-header";

export function ShellPageHeader() {
  const isAskPage = usePathname() === "/ask";

  if (isAskPage) {
    return (
      <PageHeader
        title="AAOS workspace"
        description="Trusted academic context, kept close at hand."
        titleAs="span"
        mobileNavigation={<MobileNav />}
      />
    );
  }

  return <PageHeader mobileNavigation={<MobileNav />} />;
}

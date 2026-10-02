type PageHeaderProps = Readonly<{
  title?: string;
  description?: string;
  titleAs?: "h1" | "span";
}>;

/** A page's own title. Global search and identity live in the shell's top bar. */
export function PageHeader({
  title = "Overview",
  description = "Your academic workspace",
  titleAs = "h1",
}: PageHeaderProps) {
  const Title = titleAs;
  return (
    <header className="min-w-0">
      <Title className="block text-2xl font-bold tracking-[-0.02em] text-foreground sm:text-[1.75rem]">
        {title}
      </Title>
      <p className="mt-1 text-sm text-muted-foreground">
        {description}
      </p>
    </header>
  );
}

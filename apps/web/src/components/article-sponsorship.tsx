export function ArticleSponsorship({ names, id }: { names?: string[]; id?: string }) {
  if (!names?.length) return null;
  return (
    <span
      id={id}
      className="content-type card-meta-type partner-placement-label"
      data-promotion-kind="sponsored"
      title={`Sponsored by ${names.join(", ")}`}
    >
      Sponsored<span className="sr-only"> by {names.join(", ")}</span>
    </span>
  );
}

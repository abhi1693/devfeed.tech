import { Alert, AlertDescription } from "@/components/atoms/alert";
import { Button } from "@/components/atoms/button";
export function RequestState({ loading, error, retry }: { loading?: boolean; error?: Error; retry?: () => void }) {
  if (loading) return <p role="status" className="py-12 text-center text-sm text-muted-foreground">Loading…</p>;
  if (error) return <Alert variant="destructive"><AlertDescription>{error.message}{retry && <Button variant="outline" size="sm" onClick={retry}>Retry</Button>}</AlertDescription></Alert>;
  return null;
}

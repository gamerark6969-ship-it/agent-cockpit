import { useEffect, useState } from "react";
import { getArtifactObjectUrl } from "../lib/api";

export default function AuthedImage({
  artifactId,
  alt,
  className,
  onOpen,
}: {
  artifactId: string;
  alt: string;
  className?: string;
  onOpen?: () => void;
}) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    let cancelled = false;
    let created = "";
    getArtifactObjectUrl(artifactId)
      .then((objectUrl) => {
        created = objectUrl;
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setUrl(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [artifactId]);

  if (!url) {
    return (
      <div className={`animate-pulse rounded-lg bg-zinc-800 ${className ?? "h-32 w-full"}`} />
    );
  }
  return (
    <img
      src={url}
      alt={alt}
      onClick={onOpen}
      className={className}
      style={onOpen ? { cursor: "zoom-in" } : undefined}
    />
  );
}

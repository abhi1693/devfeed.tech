import "../app/styles/video.css";
import {
  isVideo,
  videoDuration,
  videoDurationLabel,
  youtubeUrl,
  type VideoContent,
} from "@/lib/video";

/** Metadata describes the indexed video; it never implies that playback occurred. */
export function VideoMetadata({ content }: { content: VideoContent }) {
  if (!isVideo(content)) return null;
  const metadata = content.video_metadata;
  const duration = videoDuration(metadata?.duration_seconds);
  const channel = youtubeUrl(metadata?.channel_url);
  return (
    <div className="video-metadata" aria-label="Video details">
      <span className="content-type" data-content-type="video">
        Video
      </span>
      {duration && (
        <time
          dateTime={`PT${metadata!.duration_seconds}S`}
          aria-label={`Video duration: ${videoDurationLabel(metadata!.duration_seconds!)}`}
        >
          {duration}
        </time>
      )}
      {metadata?.channel_title &&
        (channel ? (
          <a
            className="video-channel"
            href={channel}
            target="_blank"
            rel="noopener noreferrer"
            title={metadata.channel_title}
          >
            {metadata.channel_title}
          </a>
        ) : (
          <span className="video-channel">{metadata.channel_title}</span>
        ))}
    </div>
  );
}

"use client";
import { useEffect, useRef, useState } from "react";

export function useNotificationSound(
  count: number,
  loading: boolean,
  enabled: boolean,
  newest: number,
) {
  const [openedAt] = useState(Date.now);
  const audio = useRef<AudioContext | null>(null);
  const previous = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!enabled || !window.AudioContext) return;
    const unlock = () => {
      audio.current ??= new AudioContext();
      void audio.current.resume().catch(() => {});
    };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
      void audio.current?.close().catch(() => {});
      audio.current = null;
    };
  }, [enabled]);
  useEffect(() => {
    if (loading) return;
    const increased = previous.current !== undefined && count > previous.current;
    previous.current = count;
    const context = audio.current;
    if (!enabled || !increased || newest <= openedAt || !context || context.state !== "running")
      return;
    const oscillator = context.createOscillator(),
      gain = context.createGain();
    oscillator.frequency.value = 660;
    gain.gain.setValueAtTime(0.04, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.18);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.2);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
  }, [count, loading, enabled, newest, openedAt]);
}

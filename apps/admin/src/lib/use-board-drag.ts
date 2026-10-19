"use client";

import { useRef, useState, type DragEvent } from "react";

/** Only in-board drags with the expected MIME identity can request a permitted transition. */
export function useBoardDrag<Item, Stage extends string>({
  type,
  disabled,
  getKey,
  getTargets,
  onDrop,
}: {
  type: string;
  disabled: boolean;
  getKey: (item: Item) => string;
  getTargets: (item: Item) => readonly Stage[];
  onDrop: (item: Item, stage: Stage) => void;
}) {
  const current = useRef<Item | null>(null);
  const [item, setItem] = useState<Item | null>(null);
  const [over, setOver] = useState<Stage | null>(null);
  function clear() {
    current.current = null;
    setItem(null);
    setOver(null);
  }
  function canDrop(stage: Stage) {
    return !disabled && item !== null && getTargets(item).includes(stage);
  }
  function cardProps(value: Item) {
    return {
      draggable: !disabled && getTargets(value).length > 0,
      onDragStart(event: DragEvent<HTMLElement>) {
        if (
          disabled ||
          !getTargets(value).length ||
          (event.target as HTMLElement).closest("button, a")
        ) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(type, getKey(value));
        event.dataTransfer.setData("text/plain", getKey(value));
        current.current = value;
        setItem(value);
      },
      onDragEnd: clear,
    };
  }
  function columnProps(stage: Stage) {
    return {
      dropAllowed: canDrop(stage),
      dropActive: over === stage,
      onDragOver(event: DragEvent<HTMLElement>) {
        if (!canDrop(stage) || current.current === null) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setOver(stage);
      },
      onDragLeave(event: DragEvent<HTMLElement>) {
        if (
          !(event.relatedTarget instanceof Node) ||
          !event.currentTarget.contains(event.relatedTarget)
        )
          setOver(null);
      },
      onDrop(event: DragEvent<HTMLElement>) {
        const value = current.current;
        if (
          value === null ||
          disabled ||
          event.dataTransfer.getData(type) !== getKey(value) ||
          !getTargets(value).includes(stage)
        )
          return;
        event.preventDefault();
        clear();
        onDrop(value, stage);
      },
    };
  }
  return { item, cardProps, columnProps };
}

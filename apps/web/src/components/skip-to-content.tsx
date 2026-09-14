"use client";

export function SkipToContent() {
  return (
    <a
      className="skip-link"
      href="#main"
      onClick={(event) => {
        const main = document.getElementById("main");
        if (!main) return;
        event.preventDefault();
        main.focus();
        main.scrollIntoView();
      }}
    >
      Skip to content
    </a>
  );
}

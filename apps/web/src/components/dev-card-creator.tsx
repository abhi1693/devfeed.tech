"use client";
import Link from "next/link";
import { useState } from "react";
import { useUser } from "./user-account";
import { DevCardPromo } from "./dev-card-promo";

export function DevCardCreator() {
  const { user, loading } = useUser();
  const [attempt, setAttempt] = useState(0);
  return (
    <section className="public-dev-card-copy dev-card-create">
      <p>YOUR STACK. YOUR STORY.</p>
      <h1>A dev card that’s yours.</h1>
      <p>Choose your name and technologies. See your card before creating an account.</p>
      {user ? (
        <Link className="button primary" href="/settings/profile">
          Create your dev card
        </Link>
      ) : (
        <button
          className="button primary"
          disabled={loading}
          onClick={() => setAttempt((value) => value + 1)}
        >
          Preview your card
        </button>
      )}
      {attempt > 0 && <DevCardPromo key={attempt} requested />}
    </section>
  );
}

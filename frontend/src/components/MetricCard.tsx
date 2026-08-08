import type { ReactNode } from "react";

type Props = {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "bad";
  icon: ReactNode;
};

export function MetricCard({ label, value, tone = "neutral", icon }: Props) {
  return (
    <section className={`metric metric-${tone}`}>
      <div className="metric-icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </section>
  );
}

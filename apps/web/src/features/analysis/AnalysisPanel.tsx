import { useState } from "react";
import type { Zone } from "../../lib/time";
import type { ReplayPlayer } from "../../streams/replayPlayer";
import { CityTrend } from "./CityTrend";
import { FeedQualityPanel } from "./FeedQualityPanel";
import { ReplaySummary } from "./ReplaySummary";
import styles from "./AnalysisPanel.module.css";

export default function AnalysisPanel({ player, zone, onSelectStation }: {
  player: ReplayPlayer | null;
  zone: Zone;
  onSelectStation(id: string): void;
}) {
  const [tab, setTab] = useState<"summary" | "trend" | "quality">(player ? "summary" : "trend");
  const tabs = [
    ...(player ? [{ key: "summary" as const, label: player.artifact.manifest.mode === "synthetic" ? "Resumo da simulação" : "Resumo da gravação" }] : []),
    { key: "trend" as const, label: "Evolução da cidade" },
    { key: "quality" as const, label: "Qualidade do feed" },
  ];
  return (
    <>
      <div className={styles.tabs} role="group" aria-label="Análise dos dados">
        {tabs.map(({ key, label }) => <button type="button" key={key} className={styles.tab} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>)}
      </div>
      {tab === "summary" && player && <ReplaySummary artifact={player.artifact} zone={zone} onSelectStation={onSelectStation} />}
      {tab === "trend" && <CityTrend zone={zone} />}
      {tab === "quality" && <FeedQualityPanel key={player?.artifact.manifest.streamId ?? "stream"} artifact={player?.artifact ?? null} zone={zone} />}
    </>
  );
}

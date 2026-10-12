import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import diagnostics from "../../public/replay/recorded/diagnostics.json";
import { FeedQualityPanel } from "../../src/features/analysis/FeedQualityPanel";
import { useStations } from "../../src/features/stations/store";
import type { FeedDiagnostics } from "../../src/lib/contracts";
import type { Artifact } from "../../src/streams/artifact";
import { catalog, feedHealth, iso, manifest } from "./factory";

const fetchDiagnostics = vi.fn();
vi.mock("../../src/streams/api", () => ({ fetchDiagnostics: (...args: unknown[]) => fetchDiagnostics(...args) }));
beforeEach(() => {
  fetchDiagnostics.mockReset();
  useStations.getState().begin({ mode: "synthetic", streamId: "test:00000000", seed: 42, periodStart: iso(0), recordedOn: null }, new Map(), "connected", false);
});
afterEach(() => { useStations.getState().clear(); });

describe("feed quality report", () => {
  it("separates successful HTTP responses from publications the engine accepts", () => {
    const artifact: Artifact = { manifest: manifest(), catalog: catalog({}), events: [], diagnostics: diagnostics as FeedDiagnostics };
    act(() => { useStations.setState({ feedHealth: feedHealth() }); });
    render(<FeedQualityPanel artifact={artifact} zone="oslo" />);
    expect(screen.getByText("249 de 249 respostas")).toBeInTheDocument();
    const valid = screen.getByText("Publicações válidas").parentElement as HTMLElement;
    expect(within(valid).getByText("225")).toBeInTheDocument();
    const rejected = screen.getByText("Publicações rejeitadas").parentElement as HTMLElement;
    expect(within(rejected).getByText("24")).toBeInTheDocument();
    expect(fetchDiagnostics).not.toHaveBeenCalled();
  });

  it("shows unavailable diagnostics on a legacy artifact without requesting the API", () => {
    render(<FeedQualityPanel artifact={{ manifest: manifest(), catalog: catalog({}), events: [] }} zone="oslo" />);
    expect(screen.getByRole("status")).toHaveTextContent("Esta gravação não inclui o relatório");
    expect(fetchDiagnostics).not.toHaveBeenCalled();
  });

  it("allows retry after the live request fails and marks simulation data", async () => {
    fetchDiagnostics.mockRejectedValueOnce(new Error("503"));
    fetchDiagnostics.mockResolvedValueOnce({ ...diagnostics, mode: "synthetic", streamId: "test:00000000" });
    render(<FeedQualityPanel artifact={null} zone="oslo" />);
    await screen.findByText(/Não foi possível consultar o relatório/);
    fireEvent.click(screen.getByRole("button", { name: "Atualizar relatório" }));
    await screen.findByRole("table", { name: "Coleta por feed no período" });
    expect(screen.getByText(/Dados de simulação/)).toBeInTheDocument();
    expect(fetchDiagnostics).toHaveBeenCalledTimes(2);
  });

  it("does not retain the old report when an explicit refresh fails", async () => {
    fetchDiagnostics.mockResolvedValueOnce(diagnostics);
    fetchDiagnostics.mockRejectedValueOnce(new Error("503"));
    render(<FeedQualityPanel artifact={null} zone="oslo" />);
    await screen.findByRole("table", { name: "Coleta por feed no período" });
    fireEvent.click(screen.getByRole("button", { name: "Atualizar relatório" }));
    await screen.findByText(/Não foi possível consultar o relatório/);
    expect(screen.queryByRole("table", { name: "Coleta por feed no período" })).not.toBeInTheDocument();
  });
});

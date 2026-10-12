import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AllStationsList } from "../../src/features/stations/AllStationsList";
import { useStations } from "../../src/features/stations/store";
import { catalogStation, event, iso } from "./factory";

function seedCatalog(size: number) {
  const catalog = Array.from({ length: size }, (_, index) => {
    const number = String(index + 1).padStart(3, "0");
    return catalogStation(`S${number}`, `Estação ${number}`);
  });
  act(() => {
    useStations.getState().begin(
      { mode: "synthetic", streamId: "test:00000000", seed: 42, periodStart: iso(0), recordedOn: null },
      new Map([["v1", catalog]]), "file", true,
    );
    useStations.getState().apply([event(1, [])]);
  });
}

const rows = () => within(screen.getByRole("list", { name: "Todas as estações" })).getAllByRole("listitem");
beforeEach(() => seedCatalog(120));

describe("station list pages", () => {
  it("searches the whole catalog, including a station beyond the visible first page", () => {
    const onQuery = vi.fn();
    const props = { onQuery, selected: null, onSelect: vi.fn() };
    const view = render(<AllStationsList {...props} query="" />);
    expect(rows()).toHaveLength(50);
    expect(screen.queryByRole("button", { name: /^Estação 100/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar estação" }), { target: { value: "Estação 100" } });
    expect(onQuery).toHaveBeenCalledWith("Estação 100");
    view.rerender(<AllStationsList {...props} query="Estação 100" />);
    expect(rows()).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^Estação 100/ })).toBeInTheDocument();
    expect(screen.getByText("Exibindo 1 de 1 resultado da busca")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mostrar mais estações" })).not.toBeInTheDocument();
  });

  it("adds the next page and returns to the first page after changing the search", () => {
    const props = { onQuery: vi.fn(), selected: null, onSelect: vi.fn() };
    const view = render(<AllStationsList {...props} query="" />);
    fireEvent.click(screen.getByRole("button", { name: "Mostrar mais estações" }));
    expect(rows()).toHaveLength(100);
    expect(screen.getByText("Exibindo 100 de 120 estações")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar estação" }), { target: { value: "Estação 120" } });
    view.rerender(<AllStationsList {...props} query="Estação 120" />);
    expect(rows()).toHaveLength(1);
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar estação" }), { target: { value: "" } });
    view.rerender(<AllStationsList {...props} query="" />);
    expect(rows()).toHaveLength(50);
    fireEvent.click(screen.getByRole("button", { name: "Mostrar mais estações" }));
    fireEvent.click(screen.getByRole("button", { name: "Mostrar mais estações" }));
    expect(rows()).toHaveLength(120);
    expect(screen.queryByRole("button", { name: "Mostrar mais estações" })).not.toBeInTheDocument();
  });

  it("includes a selected station beyond the page and tolerates its catalog removal", () => {
    const view = render(<AllStationsList query="" onQuery={vi.fn()} selected="S100" onSelect={vi.fn()} />);
    expect(rows()).toHaveLength(100);
    expect(screen.getByRole("button", { name: /^Estação 100/ })).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByRole("button", { name: "Mostrar mais estações" }));
    expect(rows()).toHaveLength(120);
    seedCatalog(80);
    expect(rows()).toHaveLength(80);
    expect(screen.queryByRole("button", { name: /^Estação 100/ })).not.toBeInTheDocument();
    view.rerender(<AllStationsList query="" onQuery={vi.fn()} selected="S999" onSelect={vi.fn()} />);
    expect(rows()).toHaveLength(80);
    seedCatalog(0);
    expect(screen.queryByRole("list", { name: "Todas as estações" })).not.toBeInTheDocument();
    expect(screen.getByText("Exibindo 0 de 0 estações")).toBeInTheDocument();
  });
});

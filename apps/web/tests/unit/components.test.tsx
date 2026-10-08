import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { createRef } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { Header } from "../../src/app/Header";
import { StationPriorityList } from "../../src/features/priorities/StationPriorityList";
import { StationDetails } from "../../src/features/stations/StationDetails";
import { useStations } from "../../src/features/stations/store";
import { catalogStation, event, incident, iso, manifest, stationState, T0 } from "./factory";

const catalogs = new Map([["v1", ["A", "B", "C"].map((id) => catalogStation(id, `Estação ${id}`))]]);

function begin() {
  useStations.getState().begin(
    { mode: "synthetic", streamId: "test:00000000", seed: 42, periodStart: iso(0), recordedOn: null },
    catalogs,
    "file",
    true,
  );
}

const queued = (id: string, key: string, bikes = 0) =>
  stationState(id, { bikes, sortKey: key, incidents: [incident({ id: `i-${id}` })] });

function renderQueue(selected: string | null = null) {
  const heading = createRef<HTMLHeadingElement>();
  return render(
    <>
      <h2 ref={heading} tabIndex={-1}>
        Prioridades
      </h2>
      <StationPriorityList selected={selected} onSelect={() => {}} zone="oslo" headingRef={heading} />
    </>,
  );
}

const order = () =>
  within(screen.getByRole("list", { name: "Estações em prioridade" }))
    .getAllByRole("listitem")
    .map((li) => li.getAttribute("data-station"));

beforeEach(begin);

describe("priority list", () => {
  it("freezes the order while the pointer is over it and keeps updating values", () => {
    act(() => {
      useStations.getState().apply([event(1, [queued("A", "0|1|A"), queued("B", "0|2|B")])]);
    });
    renderQueue();
    expect(order()).toEqual(["A", "B"]);
    const list = screen.getByRole("list", { name: "Estações em prioridade" }).parentElement as HTMLElement;
    fireEvent.pointerEnter(list);
    act(() => {
      useStations.getState().apply([event(2, [queued("A", "0|3|A", 1), queued("C", "0|0|C")])]);
    });
    expect(order()).toEqual(["A", "B"]);
    expect(screen.getByText(/sem bicicletas, confirmado, agora 1/)).toBeInTheDocument();
    expect(screen.getByText(/1 nova na fila/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Prioridades mudaram. Reordenar" }));
    expect(order()).toEqual(["C", "B", "A"]);
  });

  it("with keyboard focus inside, only the button reorders", () => {
    act(() => {
      useStations.getState().apply([event(1, [queued("A", "0|1|A"), queued("B", "0|2|B")])]);
    });
    renderQueue();
    const button = screen.getAllByRole("button")[0] as HTMLButtonElement;
    act(() => button.focus());
    act(() => {
      useStations.getState().apply([event(2, [queued("B", "0|0|B")])]);
    });
    expect(order()).toEqual(["A", "B"]);
    expect(document.activeElement).toBe(button);
  });

  it("selects by stationId, not by position", () => {
    act(() => {
      useStations.getState().apply([event(1, [queued("A", "0|1|A"), queued("B", "0|2|B")])]);
    });
    renderQueue("B");
    act(() => {
      useStations.getState().apply([event(2, [queued("B", "0|0|B")])]);
    });
    const current = screen.getAllByRole("button").find((b) => b.getAttribute("aria-current") === "true");
    expect(current?.textContent).toContain("Estação B");
    expect(order()).toEqual(["B", "A"]);
  });
});

describe("details", () => {
  it("shows a decision as the backend sent it, even against the counts", () => {
    act(() => {
      useStations.getState().apply([event(1, [stationState("A", { bikes: 0, primary: "ok" })])]);
      useStations.getState().setNow(T0 + 60_000);
    });
    render(<StationDetails id="A" zone="oslo" />);
    expect(screen.getByText("normal")).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "Disponibilidade" });
    const bikes = within(section).getByText("Bicicletas").nextElementSibling;
    expect(bikes?.textContent).toBe("0");
  });
});

describe("header", () => {
  it("always labels the mode, from the data", () => {
    render(
      <Header
        zone="oslo"
        onZone={() => {}}
        available={["synthetic"]}
        kind="synthetic"
        onKind={() => {}}
        manifest={manifest({ parameters: { stations: 267, cadenceSeconds: 15 } })}
      />,
    );
    expect(screen.getByTestId("mode").textContent).toContain("Simulação");
    expect(screen.getByTestId("mode").textContent).toContain("seed 42");
    expect(screen.getByTestId("connection").textContent).toBe("arquivo local, seed 42");
    // Artifact modes: the quality says how the artifact was made.
    expect(screen.getByTestId("quality").textContent).toContain(
      "gerado pelo motor: 267 estações a cada 15 s",
    );
  });
});

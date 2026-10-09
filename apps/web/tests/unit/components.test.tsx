import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Header } from "../../src/app/Header";
import { MainMessage } from "../../src/app/MainMessage";
import { StationPriorityList } from "../../src/features/priorities/StationPriorityList";
import { StationDetails } from "../../src/features/stations/StationDetails";
import { useStations } from "../../src/features/stations/store";
import { catalogStation, event, feedHealth, incident, iso, manifest, snapshot, stationState, T0 } from "./factory";

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

describe("details in the server modes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows a heartbeat outage while the station is unchanged and its refreshed history is pending", async () => {
    const body = {
      streamId: "test:00000000",
      stationId: "A",
      windowStart: iso(0),
      windowEnd: iso(120),
      points: [{ sourceUpdatedAt: iso(0), bikesAvailable: 4, docksAvailable: 16, quality: "valid", primaryCondition: "ok" }],
      incidents: [], gaps: [], closed: [],
    };
    const fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true, status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => body,
      })
      .mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetch);
    act(() => {
      useStations.getState().beginSnapshot(snapshot(5, { feed: { ...feedHealth(), stateSince: iso(0) } }), "test:00000000");
      useStations.getState().setConnection("connected");
      useStations.getState().setNow(T0 + 180_000);
    });
    render(<StationDetails id="A" zone="oslo" />);
    await screen.findByText(/bicicletas entre 4 e 4, 0 mudanças/);
    const station = useStations.getState().stations.get("A");
    act(() => {
      useStations.getState().applyHeartbeat({
        streamId: "test:00000000", headSequence: 5, serverTime: iso(180),
        lastSourceUpdatedAt: iso(0), feedState: "stale", stateSince: iso(120),
      });
    });
    expect(useStations.getState().stations.get("A")).toBe(station);
    expect(screen.getByText(/sem dado por 1 min/)).toBeInTheDocument();
    await act(() => vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2)));
  });

  it("draws the timeline from /history and drops one of another stream", async () => {
    const history = (streamId: string) => ({
      streamId,
      stationId: "A",
      windowStart: iso(0),
      windowEnd: iso(3600),
      points: [
        { sourceUpdatedAt: iso(0), bikesAvailable: 4, docksAvailable: 16, quality: "valid", primaryCondition: "ok" },
        { sourceUpdatedAt: iso(600), bikesAvailable: 2, docksAvailable: 18, quality: "valid", primaryCondition: "ok" },
      ],
      incidents: [],
      gaps: [{ start: iso(1200), end: iso(1800) }],
      closed: [],
    });
    let body = history("other:11111111");
    const fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => body,
    }));
    vi.stubGlobal("fetch", fetch);
    act(() => {
      useStations.getState().beginSnapshot(snapshot(5), "test:00000000");
      useStations.getState().setConnection("connected");
    });
    const { rerender } = render(<StationDetails id="A" zone="oslo" />);
    expect(screen.getByText("Carregando a linha do tempo.")).toBeInTheDocument();
    await act(() => vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1)));
    expect(String((fetch.mock.calls[0] as unknown[])[0])).toBe("/api/v1/stations/A/history");
    expect(screen.getByText("Carregando a linha do tempo.")).toBeInTheDocument();

    body = history("test:00000000");
    act(() => {
      useStations.getState().apply([event(6, [stationState("A", { bikes: 2 })])]);
    });
    rerender(<StationDetails id="A" zone="oslo" />);
    expect(await screen.findByText(/bicicletas entre 2 e 4, 2 mudanças, sem dado por 10 min/)).toBeInTheDocument();
  });
});

describe("source state since", () => {
  it("leaves out the start when the payload has none", () => {
    act(() => {
      useStations.getState().beginSnapshot(snapshot(5, { feed: feedHealth("stale") }), "test:00000000");
      useStations.getState().setConnection("connected");
    });
    render(<MainMessage zone="oslo" onRetry={() => {}} />);
    renderQueue();
    expect(screen.getByText("Fonte vencida.")).toBeInTheDocument();
    expect(screen.getByText("Dados vencidos. A fila é do último dado válido.")).toBeInTheDocument();

    // An older event without stateSince: its committedAt is not taken as the start.
    act(() => {
      useStations.getState().apply([event(6, [], { feed: feedHealth("quarantined"), at: 0 })]);
    });
    expect(screen.getByText("O formato do feed mudou e os dados estão em quarentena.")).toBeInTheDocument();

    act(() => {
      useStations.getState().apply([event(7, [], { feed: { ...feedHealth("quarantined"), stateSince: iso(-60) } })]);
    });
    expect(screen.getByText(/em quarentena desde 00:14:00/)).toBeInTheDocument();
    expect(screen.getByText(/Dados vencidos desde 00:14:00/)).toBeInTheDocument();
  });

  it("shows the start a heartbeat brings when only the server's rule made the source stale", () => {
    act(() => {
      useStations.getState().beginSnapshot(snapshot(5, { feed: { ...feedHealth(), stateSince: iso(-600) } }), "test:00000000");
      useStations.getState().setConnection("connected");
    });
    render(<MainMessage zone="oslo" onRetry={() => {}} />);
    renderQueue();
    const beat = { serverTime: iso(120), streamId: "test:00000000", headSequence: 5, lastSourceUpdatedAt: iso(0) };
    act(() => {
      useStations.getState().applyHeartbeat({ ...beat, feedState: "stale", stateSince: iso(60) });
    });
    expect(screen.getByText(/^Fonte vencida/).textContent).toBe("Fonte vencida desde 00:16:00 (horário de Oslo, UTC+2).");
    expect(screen.getByText(/Dados vencidos desde 00:16:00 \(horário de Oslo/)).toBeInTheDocument();
  });

  it("shows the start the server sends with the snapshot", () => {
    const feed = { ...feedHealth("stale"), stateSince: iso(-600) };
    act(() => {
      useStations.getState().beginSnapshot(snapshot(5, { feed }), "test:00000000");
      useStations.getState().setConnection("connected");
    });
    render(<MainMessage zone="oslo" onRetry={() => {}} />);
    renderQueue();
    expect(screen.getByText(/^Fonte vencida/).textContent).toBe("Fonte vencida desde 00:05:00 (horário de Oslo, UTC+2).");
    expect(screen.getByText(/Dados vencidos desde 00:05:00 \(horário de Oslo/)).toBeInTheDocument();
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

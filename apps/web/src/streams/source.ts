/** Where station events come from: ReplayPlayer now, LiveSseSource with the backend. The
 * app only starts and stops a source; what the source does with time is its own. */
export interface StationEventSource {
  stop(): void;
}

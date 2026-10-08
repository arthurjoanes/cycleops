/** Row renders, counted for the frontend load test. One increment per render. */
export const renderCount = { rows: 0 };

export function countRowRender(): void {
  renderCount.rows += 1;
}

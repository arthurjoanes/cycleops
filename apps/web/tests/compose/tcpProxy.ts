import { type AddressInfo, connect, createServer, type Socket } from "node:net";

/**
 * TCP proxy in front of the edge. freeze() stops both directions of every connection open
 * at that moment and keeps the sockets open, like a dead NAT entry: the browser sees no
 * error, only silence. Connections opened after it pass normally.
 */
export async function startProxy(targetPort: number) {
  const sockets = new Set<Socket>();
  let flowing: [Socket, Socket][] = [];
  const server = createServer((client) => {
    const upstream = connect(targetPort, "127.0.0.1");
    for (const socket of [client, upstream]) {
      sockets.add(socket);
      socket.on("error", () => {
        client.destroy();
        upstream.destroy();
      });
      socket.on("close", () => sockets.delete(socket));
    }
    client.pipe(upstream);
    upstream.pipe(client);
    flowing.push([client, upstream]);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    port: (server.address() as AddressInfo).port,
    freeze() {
      for (const [client, upstream] of flowing) {
        client.unpipe(upstream);
        upstream.unpipe(client);
        client.pause();
        upstream.pause();
      }
      flowing = [];
    },
    async close() {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

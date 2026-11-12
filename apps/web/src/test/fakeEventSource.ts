/**
 * A controllable EventSource, which jsdom lacks, standing in for a run's stream.
 *
 * It keeps what the hook depends on: `lastEventId` on named events, `readyState`,
 * and `error` both when the browser will retry (CONNECTING) and when it gives up
 * (CLOSED).
 */

type Listener = (event: MessageEvent<string>) => void;

export class FakeEventSource {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSED = 2;
	static instances: FakeEventSource[] = [];

	readonly url: string;
	readyState: number = FakeEventSource.CONNECTING;
	/** The id a test gives the next event it sends, when it lets the stream count. */
	nextId = 1;
	onopen: ((event: Event) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	private readonly listeners = new Map<string, Set<Listener>>();

	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}

	static latest(): FakeEventSource {
		const latest = FakeEventSource.instances.at(-1);
		if (latest === undefined) {
			throw new Error("no EventSource has been opened");
		}
		return latest;
	}

	static reset(): void {
		FakeEventSource.instances = [];
	}

	addEventListener(type: string, listener: Listener): void {
		const set = this.listeners.get(type) ?? new Set<Listener>();
		set.add(listener);
		this.listeners.set(type, set);
	}

	removeEventListener(type: string, listener: Listener): void {
		this.listeners.get(type)?.delete(listener);
	}

	close(): void {
		this.readyState = FakeEventSource.CLOSED;
	}

	get isClosed(): boolean {
		return this.readyState === FakeEventSource.CLOSED;
	}

	/** The server accepted the connection. */
	open(): void {
		this.readyState = FakeEventSource.OPEN;
		this.onopen?.(new Event("open"));
	}

	/** The server sent `id: <id>`, `event: <type>` and a JSON `data:` line. */
	emit(type: string, id: number, data: unknown): void {
		if (this.readyState === FakeEventSource.CLOSED) {
			return;
		}
		const event = new MessageEvent<string>(type, {
			data: JSON.stringify(data),
			lastEventId: String(id),
		});
		for (const listener of this.listeners.get(type) ?? []) {
			listener(event);
		}
	}

	/** The connection dropped and the browser is retrying by itself. */
	drop(): void {
		this.readyState = FakeEventSource.CONNECTING;
		this.onerror?.(new Event("error"));
	}

	/** The browser gave up on this connection (a non-200 answer, say). */
	fail(): void {
		this.readyState = FakeEventSource.CLOSED;
		this.onerror?.(new Event("error"));
	}
}

/**
 * Follow one run's event stream: connect, resume, de-duplicate, throttle.
 *
 * EventSource reconnects by itself with `Last-Event-ID`, and the API replays what
 * was missed. This hook adds the rest: a new stream when the browser gives up,
 * each event applied once, and progress redrawn at most twice a second.
 */

import { useEffect, useRef, useState } from "react";
import {
	applyRunEvent,
	initialRunState,
	parseRunEvent,
	RUN_EVENT_NAMES,
	type RunState,
} from "./runEvents";

export type Connection = "connecting" | "live" | "reconnecting" | "closed";

/** The parts of EventSource this hook uses, so a test can supply its own stream. */
export interface EventSourceLike {
	readonly readyState: number;
	onopen: ((event: Event) => void) | null;
	onerror: ((event: Event) => void) | null;
	addEventListener(
		type: string,
		listener: (event: MessageEvent<string>) => void,
	): void;
	close(): void;
}

export interface RunEventsOptions {
	createEventSource?: (url: string) => EventSourceLike;
}

const CLOSED = 2;
const PROGRESS_INTERVAL_MS = 500;
const FIRST_RETRY_MS = 1000;
const LONGEST_RETRY_MS = 30_000;

const browserEventSource = (url: string): EventSourceLike =>
	new EventSource(url);

export function useRunEvents(
	url: string,
	options: RunEventsOptions = {},
): { state: RunState; connection: Connection } {
	const [state, setState] = useState<RunState>(initialRunState);
	const [connection, setConnection] = useState<Connection>("connecting");
	// A ref, not a dependency: callers pass an inline function, and a new one
	// on every render must not tear down and reopen the stream.
	const createRef = useRef(options.createEventSource ?? browserEventSource);

	useEffect(() => {
		// Applied at once, so de-duplication is exact; React only sees it when
		// the throttle allows.
		let current = initialRunState;
		let source: EventSourceLike | null = null;
		let isStopped = false;
		let failedAttempts = 0;
		let retryTimer: ReturnType<typeof setTimeout> | undefined;
		let progressTimer: ReturnType<typeof setTimeout> | undefined;
		let lastProgressDrawnAt = Number.NEGATIVE_INFINITY;

		const draw = () => {
			clearTimeout(progressTimer);
			progressTimer = undefined;
			setState(current);
		};

		const drawProgress = () => {
			const wait = lastProgressDrawnAt + PROGRESS_INTERVAL_MS - Date.now();
			if (wait <= 0) {
				lastProgressDrawnAt = Date.now();
				draw();
			} else if (progressTimer === undefined) {
				progressTimer = setTimeout(() => {
					lastProgressDrawnAt = Date.now();
					draw();
				}, wait);
			}
		};

		const stop = () => {
			isStopped = true;
			clearTimeout(retryTimer);
			source?.close();
		};

		const onEvent = (message: MessageEvent<string>) => {
			const event = parseRunEvent(
				message.type,
				message.lastEventId,
				message.data,
			);
			const next = event === null ? current : applyRunEvent(current, event);
			if (event === null || next === current) {
				return;
			}
			current = next;
			if (event.type === "stage.progress") {
				drawProgress();
			} else {
				draw();
			}
			// The server closes the stream after job.finished. EventSource would
			// read that as a drop and reconnect, replaying the whole run and
			// closing again, forever. Closing it here ends that loop.
			if (current.isFinished) {
				stop();
				setConnection("closed");
			}
		};

		const connect = () => {
			const opened = createRef.current(
				current.lastEventId === 0
					? url
					: `${url}?last_event_id=${current.lastEventId}`,
			);
			source = opened;
			opened.onopen = () => {
				failedAttempts = 0;
				setConnection("live");
			};
			opened.onerror = () => {
				if (isStopped) {
					return;
				}
				setConnection("reconnecting");
				if (opened.readyState === CLOSED) {
					// The browser has given up on this connection. A new one
					// cannot send Last-Event-ID, so it names the id in the URL;
					// whatever the server replays anyway, applyRunEvent drops.
					opened.close();
					const delay = Math.min(
						LONGEST_RETRY_MS,
						FIRST_RETRY_MS * 2 ** failedAttempts,
					);
					failedAttempts += 1;
					retryTimer = setTimeout(connect, delay);
				}
			};
			for (const name of RUN_EVENT_NAMES) {
				opened.addEventListener(name, onEvent);
			}
		};

		connect();
		return () => {
			stop();
			clearTimeout(progressTimer);
		};
	}, [url]);

	return { state, connection };
}

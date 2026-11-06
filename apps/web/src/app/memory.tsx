/**
 * State that lives only in this page's memory: unsent drafts, and whether the
 * session has ended under us.
 *
 * Memory, not storage, on purpose. A draft can hold the sensitive question a
 * person has not sent yet; it must survive a sign-in (UI_UX_SPEC §6.1) but
 * never outlive the tab (§13.2).
 */

import {
	createContext,
	type ReactNode,
	useContext,
	useSyncExternalStore,
} from "react";

class Cell<T> {
	private value: T;
	private readonly listeners = new Set<() => void>();

	constructor(initial: T) {
		this.value = initial;
	}

	get = (): T => this.value;

	set = (value: T): void => {
		this.value = value;
		for (const listener of this.listeners) {
			listener();
		}
	};

	subscribe = (listener: () => void): (() => void) => {
		this.listeners.add(listener);
		return () => this.listeners.delete(listener);
	};
}

export class PageMemory {
	readonly isSignedOut = new Cell(false);
	/**
	 * How thorough a just-created review should be, until its page starts the
	 * plan. Two steps because the plan call waits on the model: the review page
	 * shows the question while it does, and offers a retry if it fails.
	 */
	readonly planModes = new Map<string, "shallow" | "deep">();
	private readonly drafts = new Map<string, Cell<string>>();

	draft(key: string): Cell<string> {
		const existing = this.drafts.get(key);
		if (existing !== undefined) {
			return existing;
		}
		const cell = new Cell("");
		this.drafts.set(key, cell);
		return cell;
	}
}

const MemoryContext = createContext<PageMemory | null>(null);

export function MemoryProvider({
	memory,
	children,
}: {
	memory: PageMemory;
	children: ReactNode;
}) {
	return <MemoryContext value={memory}>{children}</MemoryContext>;
}

export function usePageMemory(): PageMemory {
	const memory = useContext(MemoryContext);
	if (memory === null) {
		throw new Error("usePageMemory must be used inside <MemoryProvider>");
	}
	return memory;
}

function useCell<T>(cell: Cell<T>): [T, (value: T) => void] {
	return [useSyncExternalStore(cell.subscribe, cell.get), cell.set];
}

/** Unsent text for one composer, kept until it is sent or the tab closes. */
export function useDraft(key: string): [string, (draft: string) => void] {
	return useCell(usePageMemory().draft(key));
}

export function useSignedOutNotice(): [
	boolean,
	(isSignedOut: boolean) => void,
] {
	return useCell(usePageMemory().isSignedOut);
}

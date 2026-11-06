import { useEffect, useState } from "react";

const secondsUntil = (until: number | null): number =>
	until === null ? 0 : Math.max(0, Math.ceil((until - Date.now()) / 1000));

/**
 * Seconds left until `until` (a Date.now() value), ticking once a second; 0
 * once it has passed or when there is nothing to wait for.
 */
export function useCountdown(until: number | null): number {
	const [seconds, setSeconds] = useState(() => secondsUntil(until));

	useEffect(() => {
		setSeconds(secondsUntil(until));
		if (until === null) {
			return;
		}
		const timer = setInterval(() => {
			const left = secondsUntil(until);
			setSeconds(left);
			if (left === 0) {
				clearInterval(timer);
			}
		}, 1000);
		return () => clearInterval(timer);
	}, [until]);

	return seconds;
}

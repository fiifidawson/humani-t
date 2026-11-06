import { chakra } from "@chakra-ui/react";
import { useMemo } from "react";
import { encode } from "uqr";

interface QrCodeProps {
	value: string;
	label: string;
}

/**
 * A QR code drawn as one SVG path: no image request, no data: URI, no
 * innerHTML, so nothing the CSP has to allow. Dark modules on a light square
 * in both themes, because phone cameras expect that contrast.
 */
export function QrCode({ value, label }: QrCodeProps) {
	const { size, path } = useMemo(() => {
		const qr = encode(value, { ecc: "M", border: 2 });
		const modules = qr.data.flatMap((row, y) =>
			row.flatMap((isDark, x) => (isDark ? [`M${x} ${y}h1v1h-1z`] : [])),
		);
		return { size: qr.size, path: modules.join("") };
	}, [value]);

	return (
		<chakra.svg
			role="img"
			w="44"
			h="44"
			p="2"
			rounded="card"
			bg="#FFFFFF"
			borderWidth="1px"
			borderColor="border"
			aria-label={label}
			viewBox={`0 0 ${size} ${size}`}
			shapeRendering="crispEdges"
		>
			<rect fill="#FFFFFF" width={size} height={size} />
			<path fill="#000000" d={path} />
		</chakra.svg>
	);
}

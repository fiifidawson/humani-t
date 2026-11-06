import { chakra, type HTMLChakraProps } from "@chakra-ui/react";
import { Monitor, Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { type ThemeChoice, useTheme } from "../app/preferences";

const NEXT: Readonly<Record<ThemeChoice, ThemeChoice>> = {
	system: "light",
	light: "dark",
	dark: "system",
};
const ICON: Readonly<Record<ThemeChoice, ReactNode>> = {
	system: <Monitor size={18} />,
	light: <Sun size={18} />,
	dark: <Moon size={18} />,
};
const WORD: Readonly<Record<ThemeChoice, string>> = {
	system: "System",
	light: "Light",
	dark: "Dark",
};

type ThemeButtonProps = Omit<
	HTMLChakraProps<"button">,
	"type" | "onClick" | "children" | "aria-label" | "title"
>;

/** Cycle system → light → dark. The Account page offers the same choice as radio buttons. */
export function ThemeButton(props: ThemeButtonProps) {
	const [theme, setTheme] = useTheme();
	return (
		<chakra.button
			{...props}
			type="button"
			aria-label={`Theme: ${WORD[theme]}. Change theme`}
			title="Change theme"
			onClick={() => setTheme(NEXT[theme])}
		>
			{ICON[theme]}
			<span className="rail-label">Theme: {WORD[theme]}</span>
		</chakra.button>
	);
}

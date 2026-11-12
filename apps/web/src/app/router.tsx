/**
 * The site map (UI_UX_SPEC §3.2, the P1 part of it) and who may reach what.
 *
 * Route guards decide only where to send the browser: the API decides what a
 * caller may see, and answers 401 or 404 whatever the browser believed.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
	createRootRouteWithContext,
	createRoute,
	createRouter,
	lazyRouteComponent,
	type ParsedLocation,
	type RouterHistory,
	redirect,
} from "@tanstack/react-router";
import type { ApiClient } from "../api/client";
import { isApiError } from "../api/problem";
import type { components } from "../api/schema.gen";
import { safeRedirect } from "../features/auth/redirect";
import { SignInPage } from "../features/auth/SignInPage";
import { HomePage } from "../features/projects/HomePage";
import { AppShell } from "../shell/AppShell";
import {
	NotFoundPage,
	RouteErrorPage,
	RoutePending,
} from "../shell/RouteStates";
import { sessionQuery } from "./queries";

type PaperLabel = "include" | "exclude" | "uncertain" | "unscreened";

export interface RouterContext {
	api: ApiClient;
	queryClient: QueryClient;
}

export interface SignInSearch {
	redirect?: string | undefined;
	step?: "second-factor" | undefined;
}

type SessionCheck =
	| components["schemas"]["CurrentSessionOut"]
	| "signed-out"
	| "second-factor";

async function checkSession({
	api,
	queryClient,
}: RouterContext): Promise<SessionCheck> {
	try {
		return await queryClient.ensureQueryData(sessionQuery(api));
	} catch (error) {
		if (isApiError(error, "signed-out")) {
			return "signed-out";
		}
		if (isApiError(error, "mfa-required")) {
			return "second-factor";
		}
		throw error;
	}
}

function toSignIn(
	location: ParsedLocation,
	check: "signed-out" | "second-factor",
) {
	const search: SignInSearch = { redirect: location.href };
	if (check === "second-factor") {
		search.step = "second-factor";
	}
	return redirect({ to: "/sign-in", search });
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
	notFoundComponent: NotFoundPage,
});

const signInRoute = createRoute({
	getParentRoute: () => rootRoute,
	path: "sign-in",
	// Both keys are always returned, undefined when rejected. The router lays
	// each route's validated search over its parent's raw search, so a key
	// left out here would let the unchecked value through from the URL.
	validateSearch: (search: Record<string, unknown>): SignInSearch => ({
		redirect: safeRedirect(search["redirect"]),
		step: search["step"] === "second-factor" ? "second-factor" : undefined,
	}),
	beforeLoad: async ({ context, search }) => {
		const check = await checkSession(context);
		if (typeof check === "object") {
			throw check.mfa_enrolment_required
				? redirect({ to: "/enrol" })
				: redirect({ href: safeRedirect(search.redirect) ?? "/" });
		}
	},
	component: SignInPage,
});

const enrolRoute = createRoute({
	getParentRoute: () => rootRoute,
	path: "enrol",
	beforeLoad: async ({ context, location }) => {
		const check = await checkSession(context);
		if (typeof check !== "object") {
			throw toSignIn(location, check);
		}
		if (!check.mfa_enrolment_required) {
			throw redirect({ to: "/" });
		}
	},
	component: lazyRouteComponent(
		() => import("../features/auth/EnrolPage"),
		"EnrolPage",
	),
});

// Opened from emailed links, signed in or not; the token travels in the fragment.
const acceptInvitationRoute = createRoute({
	getParentRoute: () => rootRoute,
	path: "accept-invitation",
	component: lazyRouteComponent(
		() => import("../features/auth/AcceptInvitationPage"),
		"AcceptInvitationPage",
	),
});

const resetPasswordRoute = createRoute({
	getParentRoute: () => rootRoute,
	path: "reset-password",
	component: lazyRouteComponent(
		() => import("../features/auth/ResetPasswordPage"),
		"ResetPasswordPage",
	),
});

const signedInRoute = createRoute({
	getParentRoute: () => rootRoute,
	id: "signed-in",
	beforeLoad: async ({ context, location }) => {
		const check = await checkSession(context);
		if (typeof check !== "object") {
			throw toSignIn(location, check);
		}
		if (check.mfa_enrolment_required) {
			throw redirect({ to: "/enrol" });
		}
	},
	component: AppShell,
});

const homeRoute = createRoute({
	getParentRoute: () => signedInRoute,
	path: "/",
	component: HomePage,
});

const projectsRoute = createRoute({
	getParentRoute: () => signedInRoute,
	path: "projects",
	component: lazyRouteComponent(
		() => import("../features/projects/ProjectsPage"),
		"ProjectsPage",
	),
});

const newProjectRoute = createRoute({
	getParentRoute: () => signedInRoute,
	path: "projects/new",
	component: lazyRouteComponent(
		() => import("../features/projects/NewProjectPage"),
		"NewProjectPage",
	),
});

// Every page but sign-in and Home loads on first use, in its own chunk, so the
// first screen stays small (UI_UX_SPEC §13.1-13.2).
const workspaceRoute = createRoute({
	getParentRoute: () => signedInRoute,
	path: "p/$projectId",
	component: lazyRouteComponent(
		() => import("../features/projects/WorkspacePage"),
		"WorkspacePage",
	),
});

const projectHomeRoute = createRoute({
	getParentRoute: () => workspaceRoute,
	path: "/",
	component: lazyRouteComponent(
		() => import("../features/projects/WorkspacePage"),
		"ProjectHome",
	),
});

const projectSettingsRoute = createRoute({
	getParentRoute: () => workspaceRoute,
	path: "settings",
	component: lazyRouteComponent(
		() => import("../features/projects/ProjectSettingsPage"),
		"ProjectSettingsPage",
	),
});

const reviewRoute = createRoute({
	getParentRoute: () => workspaceRoute,
	path: "r/$reviewId",
	component: lazyRouteComponent(
		() => import("../features/reviews/ReviewPage"),
		"ReviewPage",
	),
});

/** The review with no canvas open: the chat alone. */
const reviewChatRoute = createRoute({
	getParentRoute: () => reviewRoute,
	path: "/",
	component: () => null,
});

export interface DraftSearch {
	version?: number | undefined;
}

export interface PapersSearch {
	run?: string | undefined;
	label?: PaperLabel | undefined;
	page?: number | undefined;
}

const PAPER_LABELS: readonly PaperLabel[] = [
	"include",
	"exclude",
	"uncertain",
	"unscreened",
];

/** A whole number from 1, or undefined: search values are input like any other. */
function positiveInteger(value: unknown): number | undefined {
	const number = typeof value === "string" ? Number(value) : value;
	return typeof number === "number" && Number.isInteger(number) && number >= 1
		? number
		: undefined;
}

const draftRoute = createRoute({
	getParentRoute: () => reviewRoute,
	path: "draft",
	validateSearch: (search: Record<string, unknown>): DraftSearch => ({
		version: positiveInteger(search["version"]),
	}),
	component: lazyRouteComponent(
		() => import("../features/reviews/DraftCanvas"),
		"DraftCanvas",
	),
});

const runRoute = createRoute({
	getParentRoute: () => reviewRoute,
	path: "run/$runId",
	component: lazyRouteComponent(
		() => import("../features/reviews/RunCanvas"),
		"RunCanvas",
	),
});

const papersRoute = createRoute({
	getParentRoute: () => reviewRoute,
	path: "papers",
	validateSearch: (search: Record<string, unknown>): PapersSearch => ({
		run: typeof search["run"] === "string" ? search["run"] : undefined,
		label: PAPER_LABELS.find((label) => label === search["label"]),
		page: positiveInteger(search["page"]),
	}),
	component: lazyRouteComponent(
		() => import("../features/reviews/PapersCanvas"),
		"PapersCanvas",
	),
});

const threadRoute = createRoute({
	getParentRoute: () => workspaceRoute,
	path: "ask/$threadId",
	component: lazyRouteComponent(
		() => import("../features/reviews/ThreadPage"),
		"ThreadPage",
	),
});

const accountRoute = createRoute({
	getParentRoute: () => signedInRoute,
	path: "settings",
	component: lazyRouteComponent(
		() => import("../features/account/AccountPage"),
		"AccountPage",
	),
});

export interface BriefSearch {
	/** The run whose brief to show; the latest brief when left out. */
	run?: string | undefined;
	/** The source panel's tag (UI_UX_SPEC §3.2 "a passage is a query parameter"). */
	source?: string | undefined;
}

const SOURCE_TAG = /^[PD]\d{1,6}$/;

const briefRoute = createRoute({
	getParentRoute: () => reviewRoute,
	path: "brief",
	validateSearch: (search: Record<string, unknown>): BriefSearch => ({
		run: typeof search["run"] === "string" ? search["run"] : undefined,
		source:
			typeof search["source"] === "string" && SOURCE_TAG.test(search["source"])
				? search["source"]
				: undefined,
	}),
	component: lazyRouteComponent(
		() => import("../features/briefs/BriefCanvas"),
		"BriefCanvas",
	),
});

const traceRoute = createRoute({
	getParentRoute: () => reviewRoute,
	path: "trace",
	validateSearch: (
		search: Record<string, unknown>,
	): { run?: string | undefined } => ({
		run: typeof search["run"] === "string" ? search["run"] : undefined,
	}),
	component: lazyRouteComponent(
		() => import("../features/briefs/TraceCanvas"),
		"TraceCanvas",
	),
});

export interface DocumentsSearch {
	/** The document whose masked text is open. */
	document?: string | undefined;
}

const documentsRoute = createRoute({
	getParentRoute: () => workspaceRoute,
	path: "documents",
	validateSearch: (search: Record<string, unknown>): DocumentsSearch => ({
		document:
			typeof search["document"] === "string" ? search["document"] : undefined,
	}),
	component: lazyRouteComponent(
		() => import("../features/documents/Documents"),
		"DocumentsPage",
	),
});

const routeTree = rootRoute.addChildren([
	signInRoute,
	enrolRoute,
	acceptInvitationRoute,
	resetPasswordRoute,
	signedInRoute.addChildren([
		homeRoute,
		projectsRoute,
		newProjectRoute,
		workspaceRoute.addChildren([
			projectHomeRoute,
			projectSettingsRoute,
			reviewRoute.addChildren([
				reviewChatRoute,
				draftRoute,
				runRoute,
				papersRoute,
				briefRoute,
				traceRoute,
			]),
			documentsRoute,
			threadRoute,
		]),
		accountRoute,
	]),
]);

export function createAppRouter({
	api,
	queryClient,
	history,
}: RouterContext & { history?: RouterHistory }) {
	return createRouter({
		routeTree,
		context: { api, queryClient },
		...(history === undefined ? {} : { history }),
		defaultErrorComponent: RouteErrorPage,
		defaultPendingComponent: RoutePending,
		defaultPendingMs: 400,
		// Guards read the session from the query cache, which knows when it
		// is stale; the router's own cache would only add a second answer.
		defaultPreloadStaleTime: 0,
	});
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
	interface Register {
		router: AppRouter;
	}
}

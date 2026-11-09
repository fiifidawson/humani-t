/**
 * A fake humani-t API for component tests, served through MSW.
 *
 * It keeps state and applies the API's rules for real: passwords are checked,
 * a second factor is required once one is enrolled, writes need the CSRF token
 * from the `__Host-csrf` cookie, sensitive actions need a recent sign-in. A
 * fake that returned canned responses would let every screen pass whether or
 * not it sent the right request.
 *
 * Shapes follow the API's contract tests (services/api/tests/test_mfa_api.py,
 * test_projects_api.py) and the generated schema. It is not a second
 * implementation of the API: rules the screens never exercise are left out.
 */

import { HttpResponse, http } from "msw";
import { FakeDocuments } from "./fakeDocuments";
import {
	jsonBody,
	nextId,
	nextNumber,
	noContent,
	problem,
	text,
} from "./fakeHttp";
import { FakeReviews } from "./fakeReviews";

const API = "*/api/v1";
const CSRF_COOKIE = "__Host-csrf";

export const TOTP_CODE = "123456";
export const PASSWORD = "correct horse battery staple";

type Stage = "pending" | "enrolment" | "full";
type Role = "owner" | "reviewer" | "viewer";

interface FakePasskey {
	id: string;
	name: string;
	created_at: string;
	last_used_at: string | null;
}

interface FakeUser {
	id: string;
	email: string;
	password: string;
	display_name: string;
	is_admin: boolean;
	hasTotp: boolean;
	passkeys: FakePasskey[];
	recoveryCodes: string[];
}

interface FakeSession {
	id: string;
	userId: string;
	stage: Stage;
	isRecent: boolean;
	userAgent: string;
}

interface FakeProject {
	id: string;
	name: string;
	role: Role;
	created_at: string;
	archived_at: string | null;
}

/** Someone in a project other than the signed-in person, whose role is the project's `role`. */
interface FakeMember {
	userId: string;
	role: Role;
	addedAt: string;
}

const ROLES: readonly string[] = ["owner", "reviewer", "viewer"];
const LAST_OWNER =
	"A project needs at least one owner. Make someone else an owner first.";

export interface RecordedRequest {
	method: string;
	path: string;
	csrfToken: string | null;
	contentType: string | null;
	body: unknown;
}

interface UserOptions {
	email?: string;
	displayName?: string;
	hasTotp?: boolean;
	hasPasskey?: boolean;
}

export class FakeApi {
	readonly users: FakeUser[] = [];
	readonly projects: FakeProject[] = [];
	/** Each project's other members, by project id, earliest added first. */
	readonly members = new Map<string, FakeMember[]>();
	readonly requests: RecordedRequest[] = [];
	readonly otherSessions: {
		id: string;
		userAgent: string;
		lastSeen: string;
	}[] = [];
	session: FakeSession | null = null;
	/** Reviews, plans, runs, gates and papers (fakeReviews.ts). */
	readonly reviews = new FakeReviews({
		refuseUnlessSignedIn: () => this.refuseUnlessSignedIn(),
		roleIn: (projectId) =>
			this.projects.find((p) => p.id === projectId)?.role ?? null,
		userId: () => this.session?.userId ?? null,
	});
	/** A project's uploaded documents (fakeDocuments.ts). */
	readonly documents = new FakeDocuments({
		refuseUnlessSignedIn: () => this.refuseUnlessSignedIn(),
		roleIn: (projectId) =>
			this.projects.find((p) => p.id === projectId)?.role ?? null,
		userId: () => this.session?.userId ?? null,
	});
	csrfToken = "csrf-1";
	/** Seconds the next sign-in attempt must wait, as `Retry-After`. */
	throttleNextSignIn: number | null = null;
	pendingTotpSecret: string | null = null;

	addUser(options: UserOptions = {}): FakeUser {
		const user: FakeUser = {
			id: nextId("u"),
			email: options.email ?? "amara@example.org",
			password: PASSWORD,
			display_name: options.displayName ?? "Amara N.",
			is_admin: false,
			hasTotp: options.hasTotp ?? false,
			passkeys: options.hasPasskey
				? [
						{
							id: nextId("pk"),
							name: "Work laptop",
							last_used_at: null,
							created_at: "2026-10-01T09:00:00Z",
						},
					]
				: [],
			recoveryCodes:
				options.hasTotp || options.hasPasskey
					? ["AAAA-BBBB-CCCC-DDDD", "EEEE-FFFF-GGGG-HHHH"]
					: [],
		};
		this.users.push(user);
		return user;
	}

	/** Start the test already signed in, by default at full strength with a recent sign-in. */
	signInAs(user: FakeUser, stage: Stage = "full"): void {
		this.startSession(user, stage);
	}

	addProject(
		name: string,
		role: Role = "owner",
		createdAt = "2026-11-01T10:00:00Z",
	): FakeProject {
		const project: FakeProject = {
			id: nextId("p"),
			name,
			role,
			created_at: createdAt,
			archived_at: null,
		};
		this.projects.push(project);
		return project;
	}

	addMember(
		project: FakeProject,
		user: FakeUser,
		role: Role,
		addedAt = "2026-11-02T10:00:00Z",
	): FakeMember {
		const member = { userId: user.id, role, addedAt };
		this.othersIn(project.id).push(member);
		return member;
	}

	expireSession(): void {
		this.session = null;
	}

	makeSignInStale(): void {
		if (this.session !== null) {
			this.session.isRecent = false;
		}
	}

	requestsTo(method: string, path: string): RecordedRequest[] {
		return this.requests.filter(
			(r) => r.method === method && r.path === `/api/v1${path}`,
		);
	}

	private currentUser(): FakeUser | undefined {
		const session = this.session;
		return session === null
			? undefined
			: this.users.find((u) => u.id === session.userId);
	}

	private startSession(user: FakeUser, stage: Stage): void {
		this.session = {
			id: nextId("s"),
			userId: user.id,
			stage,
			isRecent: true,
			userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/140.0",
		};
		// The real API sets this with Set-Cookie. Node's fetch keeps no cookie
		// jar, so the fake writes it where the browser would have put it.
		this.csrfToken = `csrf-${nextNumber()}`;
		// biome-ignore lint/suspicious/noDocumentCookie: the fake stands in for the server's Set-Cookie
		document.cookie = `${CSRF_COOKIE}=${this.csrfToken}; Secure; Path=/; SameSite=Strict`;
	}

	private endSession(): void {
		this.session = null;
		// biome-ignore lint/suspicious/noDocumentCookie: the fake stands in for the server's Set-Cookie
		document.cookie = `${CSRF_COOKIE}=; Secure; Path=/; Max-Age=0`;
	}

	private methodsFor(user: FakeUser): string[] {
		return [
			...(user.passkeys.length > 0 ? ["passkey"] : []),
			...(user.hasTotp ? ["totp"] : []),
			...(user.recoveryCodes.length > 0 ? ["recovery_code"] : []),
		];
	}

	private userOut(user: FakeUser) {
		return {
			id: user.id,
			email: user.email,
			display_name: user.display_name,
			is_admin: user.is_admin,
		};
	}

	private completeSignIn(user: FakeUser) {
		this.startSession(user, "full");
		return HttpResponse.json({ status: "signed_in", user: this.userOut(user) });
	}

	/** Refuse unless signed in at full strength; `null` means the request may go on. */
	private refuseUnlessSignedIn(options: { allowEnrolment?: boolean } = {}) {
		const session = this.session;
		if (session === null) {
			return problem(401, "Sign in to continue.");
		}
		if (session.stage === "pending") {
			return problem(401, "Confirm it's you to continue.", {
				mfa_required: true,
			});
		}
		if (session.stage === "enrolment" && !options.allowEnrolment) {
			return problem(403, "Add a second sign-in method first.", {
				mfa_enrolment_required: true,
			});
		}
		return null;
	}

	private refuseUnlessRecent() {
		if (this.session?.isRecent === false) {
			return problem(401, "Sign in again to continue.", {
				reauthentication_required: true,
			});
		}
		return null;
	}

	/** The gate in front of a project route (api/access.py): 404, then 403, then the recent sign-in. */
	private gate(
		projectId: unknown,
		options: { isOwnersOnly?: boolean; needsRecentSignIn?: boolean } = {},
	):
		| { project: FakeProject; refused: null }
		| { project: null; refused: ReturnType<typeof problem> } {
		const refused = this.refuseUnlessSignedIn();
		if (refused !== null) {
			return { project: null, refused };
		}
		const project = this.projects.find((p) => p.id === projectId);
		if (project === undefined) {
			return { project: null, refused: problem(404, "No such project.") };
		}
		if (options.isOwnersOnly && project.role !== "owner") {
			return {
				project: null,
				refused: problem(403, "Your role in this project does not allow this."),
			};
		}
		const stale = options.needsRecentSignIn ? this.refuseUnlessRecent() : null;
		return stale === null
			? { project, refused: null }
			: { project: null, refused: stale };
	}

	private othersIn(projectId: string): FakeMember[] {
		const others = this.members.get(projectId) ?? [];
		this.members.set(projectId, others);
		return others;
	}

	/** Everyone in the project, the signed-in person first: they created it. */
	private memberList(project: FakeProject): FakeMember[] {
		const userId = this.session?.userId;
		const self =
			userId === undefined
				? []
				: [{ userId, role: project.role, addedAt: project.created_at }];
		return [...self, ...this.othersIn(project.id)];
	}

	/** As the API: the email goes to owners only, and is otherwise left out. */
	private memberOut(member: FakeMember, showsEmail: boolean) {
		const user = this.users.find((u) => u.id === member.userId);
		return {
			user_id: member.userId,
			display_name: user?.display_name ?? "Unknown account",
			role: member.role,
			added_at: member.addedAt,
			...(showsEmail && user !== undefined ? { email: user.email } : {}),
		};
	}

	/** `newRole` is `null` for a removal (domain/projects.py `require_an_owner_remains`). */
	private leavesNoOwner(
		project: FakeProject,
		userId: string,
		newRole: Role | null,
	): boolean {
		const members = this.memberList(project);
		const target = members.find((m) => m.userId === userId);
		if (target?.role !== "owner" || newRole === "owner") {
			return false;
		}
		return members.filter((m) => m.role === "owner").length <= 1;
	}

	private raiseToFullStrength(user: FakeUser): string[] | null {
		const wasEnrolling = this.session?.stage === "enrolment";
		this.startSession(user, "full");
		if (!wasEnrolling) {
			return null;
		}
		user.recoveryCodes = Array.from(
			{ length: 10 },
			(_, n) => `R${n}AA-BBBB-CCCC-DDDD`,
		);
		return user.recoveryCodes;
	}

	handlers() {
		const record = async (request: Request) => {
			// An upload's body is a file, recorded by its content type alone.
			const isUpload = request.headers
				.get("content-type")
				?.startsWith("multipart/form-data");
			const body =
				request.method === "GET" || isUpload ? null : await jsonBody(request);
			this.requests.push({
				method: request.method,
				path: new URL(request.url).pathname,
				csrfToken: request.headers.get("x-csrf-token"),
				contentType: request.headers.get("content-type"),
				body,
			});
			const isUnsafe = !["GET", "HEAD", "OPTIONS"].includes(request.method);
			if (
				isUnsafe &&
				this.session !== null &&
				request.headers.get("x-csrf-token") !== this.csrfToken
			) {
				return problem(403, "Missing or invalid CSRF token.");
			}
			return null;
		};

		return [
			http.all(`${API}/*`, async ({ request }) => {
				const refused = await record(request);
				return refused ?? undefined;
			}),

			http.post(`${API}/auth/login`, async ({ request }) => {
				if (this.throttleNextSignIn !== null) {
					const seconds = this.throttleNextSignIn;
					this.throttleNextSignIn = null;
					return problem(
						429,
						"Too many attempts.",
						{},
						{ "retry-after": String(seconds) },
					);
				}
				const body = await jsonBody(request);
				const user = this.users.find((u) => u.email === text(body, "email"));
				if (user === undefined || user.password !== text(body, "password")) {
					return problem(401, "The email or password is incorrect.");
				}
				const methods = this.methodsFor(user);
				if (methods.length > 0) {
					this.startSession(user, "pending");
					return HttpResponse.json({ status: "mfa_required", methods });
				}
				this.startSession(user, "enrolment");
				return HttpResponse.json({
					status: "mfa_enrolment_required",
					user: this.userOut(user),
				});
			}),

			http.post(`${API}/auth/logout`, () => {
				this.endSession();
				return noContent();
			}),

			http.get(`${API}/auth/session`, () => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				const user = this.currentUser();
				if (refused !== null || user === undefined || this.session === null) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				return HttpResponse.json({
					user: this.userOut(user),
					session: {
						id: this.session.id,
						created_at: "2026-11-03T08:00:00Z",
						last_seen_at: "2026-11-03T09:00:00Z",
						user_agent: this.session.userAgent,
						current: true,
						aal: this.session.stage === "full" ? 2 : 1,
					},
					mfa_enrolment_required: this.session.stage === "enrolment",
				});
			}),

			http.post(`${API}/auth/reauthenticate`, async ({ request }) => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const body = await jsonBody(request);
				if (text(body, "password") !== this.currentUser()?.password) {
					return problem(401, "The password is incorrect.");
				}
				if (this.session !== null) {
					this.session.isRecent = true;
				}
				return noContent();
			}),

			http.post(`${API}/auth/mfa/passkey/options`, () => {
				const user = this.currentUser();
				if (this.session?.stage !== "pending" || user === undefined) {
					return problem(401, "Sign in to continue.");
				}
				return HttpResponse.json({
					options: {
						challenge: "Y2hhbGxlbmdlLWZvci1zaWduLWlu",
						rpId: "localhost",
						timeout: 60000,
						userVerification: "required",
						allowCredentials: user.passkeys.map(() => ({
							id: "Y3JlZC0x",
							type: "public-key",
						})),
					},
				});
			}),

			http.post(`${API}/auth/mfa/passkey`, async ({ request }) => {
				const user = this.currentUser();
				const credential = (await jsonBody(request))["credential"];
				if (
					this.session?.stage !== "pending" ||
					user === undefined ||
					!isAssertion(credential)
				) {
					return problem(401, "That passkey could not be verified.");
				}
				return this.completeSignIn(user);
			}),

			http.post(`${API}/auth/mfa/totp`, async ({ request }) => {
				const user = this.currentUser();
				if (this.session?.stage !== "pending" || user === undefined) {
					return problem(401, "Sign in to continue.");
				}
				const code = text(await jsonBody(request), "code");
				if (!/^\d{6}$/.test(code)) {
					return problem(422, "The request body is not valid.", {
						errors: [
							{
								loc: ["body", "code"],
								msg: "Enter the 6 digits.",
								type: "value_error",
							},
						],
					});
				}
				if (!user.hasTotp || code !== TOTP_CODE) {
					return problem(
						401,
						"That code didn't work. Codes change every 30 seconds.",
					);
				}
				return this.completeSignIn(user);
			}),

			http.post(`${API}/auth/mfa/recovery-code`, async ({ request }) => {
				const user = this.currentUser();
				if (this.session?.stage !== "pending" || user === undefined) {
					return problem(401, "Sign in to continue.");
				}
				const code = text(await jsonBody(request), "code").toUpperCase();
				const index = user.recoveryCodes.indexOf(code);
				if (index < 0) {
					return problem(401, "That recovery code didn't work.");
				}
				user.recoveryCodes.splice(index, 1);
				return this.completeSignIn(user);
			}),

			http.get(`${API}/account/sessions`, () => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				if (refused !== null || this.session === null) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				return HttpResponse.json([
					{
						id: this.session.id,
						created_at: "2026-11-03T08:00:00Z",
						last_seen_at: "2026-11-03T09:00:00Z",
						user_agent: this.session.userAgent,
						current: true,
					},
					...this.otherSessions.map((s) => ({
						id: s.id,
						created_at: "2026-10-30T08:00:00Z",
						last_seen_at: s.lastSeen,
						user_agent: s.userAgent,
						current: false,
					})),
				]);
			}),

			http.delete(`${API}/account/sessions/:sessionId`, ({ params }) => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const index = this.otherSessions.findIndex(
					(s) => s.id === params["sessionId"],
				);
				if (index < 0) {
					return problem(404, "Not Found");
				}
				this.otherSessions.splice(index, 1);
				return noContent();
			}),

			http.post(`${API}/account/sessions/revoke-others`, () => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				this.otherSessions.length = 0;
				return noContent();
			}),

			http.get(`${API}/account/mfa`, () => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				return HttpResponse.json({
					is_enrolled: user.hasTotp || user.passkeys.length > 0,
					totp: user.hasTotp
						? {
								id: "t-1",
								created_at: "2026-10-01T09:00:00Z",
								confirmed_at: "2026-10-01T09:01:00Z",
							}
						: null,
					passkeys: user.passkeys,
					recovery_codes_remaining: user.recoveryCodes.length,
				});
			}),

			http.post(`${API}/account/mfa/totp`, () => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				if (user.hasTotp) {
					return problem(409, "An authenticator app is already set up.");
				}
				this.pendingTotpSecret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
				return HttpResponse.json(
					{
						secret: this.pendingTotpSecret,
						otpauth_uri: `otpauth://totp/humani-t:${user.email}?secret=${this.pendingTotpSecret}&issuer=humani-t`,
					},
					{ status: 201 },
				);
			}),

			http.post(`${API}/account/mfa/totp/confirm`, async ({ request }) => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				if (
					this.pendingTotpSecret === null ||
					text(await jsonBody(request), "code") !== TOTP_CODE
				) {
					return problem(
						400,
						"That code didn't match. Check the time on your phone and try again.",
					);
				}
				user.hasTotp = true;
				this.pendingTotpSecret = null;
				return HttpResponse.json({
					recovery_codes: this.raiseToFullStrength(user),
				});
			}),

			http.delete(`${API}/account/mfa/totp`, () => {
				const refused =
					this.refuseUnlessSignedIn() ?? this.refuseUnlessRecent();
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				if (user.passkeys.length === 0) {
					return problem(
						409,
						"You can't remove your last sign-in method. Add another one first.",
					);
				}
				user.hasTotp = false;
				return noContent();
			}),

			http.post(`${API}/account/mfa/passkeys/options`, () => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				return HttpResponse.json({
					options: {
						challenge: "Y2hhbGxlbmdlLWZvci1yZWdpc3Rlcg",
						rp: { id: "localhost", name: "humani-t" },
						user: {
							id: "dXNlci1oYW5kbGUtMDE",
							name: user.email,
							displayName: user.display_name,
						},
						pubKeyCredParams: [{ type: "public-key", alg: -7 }],
						timeout: 60000,
						attestation: "none",
						authenticatorSelection: {
							residentKey: "preferred",
							userVerification: "required",
						},
						excludeCredentials: [],
					},
				});
			}),

			http.post(`${API}/account/mfa/passkeys`, async ({ request }) => {
				const refused = this.refuseUnlessSignedIn({ allowEnrolment: true });
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				const body = await jsonBody(request);
				if (!isRegistration(body["credential"])) {
					return problem(400, "That passkey could not be verified.");
				}
				const passkey = {
					id: nextId("pk"),
					name: text(body, "name"),
					created_at: "2026-11-03T10:00:00Z",
					last_used_at: null,
				};
				user.passkeys.push(passkey);
				return HttpResponse.json(
					{ passkey, recovery_codes: this.raiseToFullStrength(user) },
					{ status: 201 },
				);
			}),

			http.delete(`${API}/account/mfa/passkeys/:passkeyId`, ({ params }) => {
				const refused =
					this.refuseUnlessSignedIn() ?? this.refuseUnlessRecent();
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				const index = user.passkeys.findIndex(
					(p) => p.id === params["passkeyId"],
				);
				if (index < 0) {
					return problem(404, "Not Found");
				}
				if (user.passkeys.length === 1 && !user.hasTotp) {
					return problem(
						409,
						"You can't remove your last sign-in method. Add another one first.",
					);
				}
				user.passkeys.splice(index, 1);
				return noContent();
			}),

			http.post(`${API}/account/mfa/recovery-codes`, () => {
				const refused =
					this.refuseUnlessSignedIn() ?? this.refuseUnlessRecent();
				const user = this.currentUser();
				if (refused !== null || user === undefined) {
					return refused ?? problem(401, "Sign in to continue.");
				}
				user.recoveryCodes = Array.from(
					{ length: 10 },
					(_, n) => `N${n}EW-CODE-AAAA-BBBB`,
				);
				return HttpResponse.json(
					{ recovery_codes: user.recoveryCodes },
					{ status: 201 },
				);
			}),

			http.get(`${API}/projects`, ({ request }) => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const includeArchived =
					new URL(request.url).searchParams.get("include_archived") === "true";
				return HttpResponse.json(
					this.projects.filter(
						(p) => includeArchived || p.archived_at === null,
					),
				);
			}),

			http.post(`${API}/projects`, async ({ request }) => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const name = text(await jsonBody(request), "name").trim();
				if (name === "" || name.length > 200 || /[\r\n]/.test(name)) {
					return problem(422, "The request body is not valid.", {
						errors: [
							{
								loc: ["body", "name"],
								msg: "Project name must be 1 to 200 characters on one line.",
								type: "value_error",
							},
						],
					});
				}
				const project = this.addProject(name, "owner", "2026-11-03T10:30:00Z");
				return HttpResponse.json(project, {
					status: 201,
					headers: { location: `/api/v1/projects/${project.id}` },
				});
			}),

			http.get(`${API}/projects/:projectId`, ({ params }) => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const project = this.projects.find((p) => p.id === params["projectId"]);
				return project === undefined
					? problem(404, "Not Found")
					: HttpResponse.json(project);
			}),

			http.delete(`${API}/projects/:projectId`, ({ params }) => {
				const refused = this.refuseUnlessSignedIn();
				if (refused !== null) {
					return refused;
				}
				const project = this.projects.find((p) => p.id === params["projectId"]);
				if (project === undefined) {
					return problem(404, "Not Found");
				}
				if (project.role !== "owner") {
					return problem(403, "Only owners can delete a project.");
				}
				const stale = this.refuseUnlessRecent();
				if (stale !== null) {
					return stale;
				}
				this.projects.splice(this.projects.indexOf(project), 1);
				return new HttpResponse(null, { status: 204 });
			}),

			http.patch(`${API}/projects/:projectId`, async ({ params, request }) => {
				const { project, refused } = this.gate(params["projectId"], {
					isOwnersOnly: true,
				});
				if (refused !== null) {
					return refused;
				}
				const name = text(await jsonBody(request), "name");
				if (name.length < 1 || name.length > 200) {
					return invalidBody("name", "String should have 1 to 200 characters");
				}
				// The schema checks the size; the domain trims and checks the rest,
				// and its refusal is a plain 422 with no `errors` list.
				if (name.trim() === "") {
					return problem(422, "Project.name must not be blank");
				}
				if (/[\p{Cc}\p{Zl}\p{Zp}]/u.test(name.trim())) {
					return problem(
						422,
						"Project.name must not contain control characters",
					);
				}
				project.name = name.trim();
				return HttpResponse.json(project);
			}),

			// Owner only, with no recent sign-in; archiving twice keeps the first date.
			http.post(`${API}/projects/:projectId/archive`, ({ params }) => {
				const { project, refused } = this.gate(params["projectId"], {
					isOwnersOnly: true,
				});
				if (refused !== null) {
					return refused;
				}
				project.archived_at ??= "2026-11-09T10:00:00Z";
				return HttpResponse.json(project);
			}),

			http.post(`${API}/projects/:projectId/unarchive`, ({ params }) => {
				const { project, refused } = this.gate(params["projectId"], {
					isOwnersOnly: true,
				});
				if (refused !== null) {
					return refused;
				}
				project.archived_at = null;
				return HttpResponse.json(project);
			}),

			http.get(`${API}/projects/:projectId/members`, ({ params }) => {
				const { project, refused } = this.gate(params["projectId"]);
				if (refused !== null) {
					return refused;
				}
				return HttpResponse.json(
					this.memberList(project).map((m) =>
						this.memberOut(m, project.role === "owner"),
					),
				);
			}),

			http.post(
				`${API}/projects/:projectId/members`,
				async ({ params, request }) => {
					const { project, refused } = this.gate(params["projectId"], {
						isOwnersOnly: true,
						needsRecentSignIn: true,
					});
					if (refused !== null) {
						return refused;
					}
					const body = await jsonBody(request);
					const email = text(body, "email").trim().toLowerCase();
					const role = text(body, "role");
					if (!email.includes("@")) {
						return invalidBody(
							"email",
							"value is not a valid email address: An email address must have an @-sign.",
						);
					}
					if (!isRole(role)) {
						return invalidBody(
							"role",
							"Input should be 'owner', 'reviewer' or 'viewer'",
						);
					}
					// Unknown and disabled accounts get the same answer, which never repeats the email.
					const user = this.users.find((u) => u.email.toLowerCase() === email);
					if (user === undefined) {
						return problem(422, "No active account uses this email address.");
					}
					if (this.memberList(project).some((m) => m.userId === user.id)) {
						return problem(
							409,
							"This person is already a member of the project.",
						);
					}
					const member = this.addMember(
						project,
						user,
						role,
						"2026-11-03T11:00:00Z",
					);
					return HttpResponse.json(this.memberOut(member, true), {
						status: 201,
					});
				},
			),

			http.patch(
				`${API}/projects/:projectId/members/:userId`,
				async ({ params, request }) => {
					const { project, refused } = this.gate(params["projectId"], {
						isOwnersOnly: true,
						needsRecentSignIn: true,
					});
					if (refused !== null) {
						return refused;
					}
					const role = text(await jsonBody(request), "role");
					if (!isRole(role)) {
						return invalidBody(
							"role",
							"Input should be 'owner', 'reviewer' or 'viewer'",
						);
					}
					const target = this.memberList(project).find(
						(m) => m.userId === params["userId"],
					);
					if (target === undefined) {
						return problem(404, "No such member.");
					}
					if (this.leavesNoOwner(project, target.userId, role)) {
						return problem(409, LAST_OWNER, { last_owner: true });
					}
					if (target.userId === this.session?.userId) {
						project.role = role;
					} else {
						target.role = role;
					}
					return HttpResponse.json(this.memberOut({ ...target, role }, true));
				},
			),

			http.delete(
				`${API}/projects/:projectId/members/:userId`,
				({ params }) => {
					// Leaving needs neither the owner role nor a recent sign-in.
					const isLeaving = params["userId"] === this.session?.userId;
					const { project, refused } = this.gate(params["projectId"], {
						isOwnersOnly: !isLeaving,
						needsRecentSignIn: !isLeaving,
					});
					if (refused !== null) {
						return refused;
					}
					const target = this.memberList(project).find(
						(m) => m.userId === params["userId"],
					);
					if (target === undefined) {
						return problem(404, "No such member.");
					}
					if (this.leavesNoOwner(project, target.userId, null)) {
						return problem(409, LAST_OWNER, { last_owner: true });
					}
					if (isLeaving) {
						this.projects.splice(this.projects.indexOf(project), 1);
					} else {
						const others = this.othersIn(project.id);
						others.splice(others.indexOf(target), 1);
					}
					return noContent();
				},
			),

			...this.reviews.handlers(),
			...this.documents.handlers(),
		];
	}
}

function isRole(value: string): value is Role {
	return ROLES.includes(value);
}

/** The API's 422 for a body that fails its schema, naming the field. */
function invalidBody(field: string, message: string) {
	return problem(422, "The request body is not valid.", {
		errors: [{ loc: ["body", field], msg: message, type: "value_error" }],
	});
}

function isAssertion(value: unknown): boolean {
	return hasResponseFields(value, [
		"clientDataJSON",
		"authenticatorData",
		"signature",
	]);
}

function isRegistration(value: unknown): boolean {
	return hasResponseFields(value, ["clientDataJSON", "attestationObject"]);
}

function hasResponseFields(value: unknown, fields: string[]): boolean {
	if (typeof value !== "object" || value === null || !("response" in value)) {
		return false;
	}
	const { response } = value;
	return (
		typeof response === "object" &&
		response !== null &&
		fields.every(
			(field) =>
				field in response && typeof Reflect.get(response, field) === "string",
		)
	);
}

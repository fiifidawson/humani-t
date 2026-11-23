/** A small Word file built in memory: paragraphs in Word's own styles, numbering and a table. */

import { crc32 } from "node:zlib";

const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

const CONTENT_TYPES =
	'<?xml version="1.0" encoding="UTF-8"?>' +
	'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
	'<Default Extension="xml" ContentType="application/xml"/>' +
	'<Override PartName="/word/document.xml" ContentType="application/' +
	'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
	"</Types>";

const escaped = (text: string) =>
	text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

export function paragraph(
	text: string,
	options: { style?: string; isNumbered?: boolean } = {},
): string {
	const style =
		options.style === undefined ? "" : `<w:pStyle w:val="${options.style}"/>`;
	const numbering =
		options.isNumbered === true
			? '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
			: "";
	const properties =
		style === "" && numbering === ""
			? ""
			: `<w:pPr>${style}${numbering}</w:pPr>`;
	return `<w:p>${properties}<w:r><w:t xml:space="preserve">${escaped(text)}</w:t></w:r></w:p>`;
}

export function table(rows: readonly (readonly string[])[]): string {
	const body = rows
		.map(
			(row) =>
				`<w:tr>${row.map((cell) => `<w:tc>${paragraph(cell)}</w:tc>`).join("")}</w:tr>`,
		)
		.join("");
	return `<w:tbl>${body}</w:tbl>`;
}

function uint16(value: number): Buffer {
	const bytes = Buffer.alloc(2);
	bytes.writeUInt16LE(value);
	return bytes;
}

function uint32(value: number): Buffer {
	const bytes = Buffer.alloc(4);
	bytes.writeUInt32LE(value >>> 0);
	return bytes;
}

/** An uncompressed zip, as small as the format allows. */
function zip(files: Record<string, string>): Buffer {
	const local: Buffer[] = [];
	const central: Buffer[] = [];
	let offset = 0;
	for (const [name, content] of Object.entries(files)) {
		const path = Buffer.from(name);
		const data = Buffer.from(content);
		const fields = Buffer.concat([
			uint16(20),
			uint16(0),
			uint16(0),
			uint16(0),
			uint16(0x21),
			uint32(crc32(data)),
			uint32(data.length),
			uint32(data.length),
			uint16(path.length),
			uint16(0),
		]);
		const header = Buffer.concat([uint32(0x04034b50), fields, path, data]);
		central.push(
			Buffer.concat([
				uint32(0x02014b50),
				uint16(20),
				fields,
				uint16(0),
				uint16(0),
				uint16(0),
				uint32(0),
				uint32(offset),
				path,
			]),
		);
		local.push(header);
		offset += header.length;
	}
	const directory = Buffer.concat(central);
	const count = Object.keys(files).length;
	return Buffer.concat([
		...local,
		directory,
		uint32(0x06054b50),
		uint16(0),
		uint16(0),
		uint16(count),
		uint16(count),
		uint32(directory.length),
		uint32(offset),
		uint16(0),
	]);
}

/** A `.docx` whose body is `body`, raw WordprocessingML from `paragraph` and `table`. */
export function docx(body: string): Buffer {
	return zip({
		"[Content_Types].xml": CONTENT_TYPES,
		"word/document.xml":
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
			`<w:document xmlns:w="${W}"><w:body>${body}</w:body></w:document>`,
	});
}

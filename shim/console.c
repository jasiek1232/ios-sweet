/*
 * Minimal text console on the ABL splash framebuffer. With the MMU off
 * every access is Device memory, so stay aligned (-mstrict-align).
 */
#include "console.h"
#include "font.h"

#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8

volatile uint32_t *con_fb = (volatile uint32_t *)FB_BASE;
static uint32_t con_bg;
static int row;

void con_clear(uint32_t bg)
{
	for (int y = 0; y < FB_H; y++) {
		volatile uint32_t *line = con_fb + y * (FB_STRIDE / 4);
		for (int x = 0; x < FB_W; x++)
			line[x] = bg;
	}
	con_bg = bg;
	row = 0;
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > FB_W || y0 + CH_H > FB_H)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				volatile uint32_t *p = con_fb + (y0 + gy * SCALE + sy) * (FB_STRIDE / 4)
						       + x0 + gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

/* miami: tee text into the ramoops console zone (0xaf03f800) so Android can read it back */
#define LOG_BASE	0xaf03f800UL
#define LOG_CAP		0x3fff4UL
static uint32_t log_len;
static void log_sync(void)
{
	volatile uint32_t *h = (volatile uint32_t *)LOG_BASE;
	h[0] = 0x43474244;
	h[1] = 0;
	h[2] = log_len;
}
static void log_putc(char c)
{
	if (log_len < LOG_CAP)
		((volatile uint8_t *)(LOG_BASE + 12))[log_len++] = (uint8_t)c;
}
static void log_str(const char *s)
{
	while (*s)
		log_putc(*s++);
}
static void log_nl(void)
{
	log_putc('\n');
	log_sync();
}

void con_skip(void)
{
	log_nl();
	row++;
}

void con_str(const char *label, const char *s)
{
	log_str(label);
	log_putc(' ');
	log_str(s);
	log_nl();
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	log_str(label);
	log_putc(' ');
	log_str(a);
	fmt_hex(buf, va, 8);
	log_str(buf);
	if (b) {
		log_putc(' ');
		log_str(b);
		fmt_hex(buf, vb, 8);
		log_str(buf);
	}
	log_nl();
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
}

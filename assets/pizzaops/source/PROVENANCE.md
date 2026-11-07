# Art provenance and prompt set

Method: built-in image-generation tool, with `transparent_background=true`. These nine sheets were generated for this task. Each sheet contains intentionally separated objects or character poses with transparent gutters. No restaurant screenshot was cropped. Exporting from these dedicated sheets isolates already separate objects; it does not remove overlapping scene backgrounds.

The two earlier files named `a_detailed_polished_isometric_pixel_art_game_ui.png` and `a_detailed_isometric_pixel_art_scene_of_a_cozy_piz.png` were unavailable in the accessible conversation and filesystem. The retrieved conversation supplied the cozy isometric restaurant direction, not an accessible reference image. No third-party art was downloaded or copied.

## Shared furniture direction

Production game spritesheet for PizzaOps, original cozy isometric pixel art pizzeria; exactly four columns by four rows, invisible equal grid, generous transparent gutters, independently isolated objects, entire objects visible. Consistent 2:1 isometric viewpoint, upper-left lighting, warm walnut / terracotta / cream / sage palette and dark plum outlines, crisp pixel clusters. No background, floor, scene, cast shadows, labels, or visible grid. Real alpha transparency.

### kitchen.png

Row 1: brick wood-fired dome pizza oven unlit; same oven lit orange; same oven lit yellow flicker; same oven brighter flame.

Row 2: wooden stainless prep counter; refrigerated topping station with six ingredient bins; pantry shelves with jars and flour sacks; cream green double-door fridge.

Row 3: wooden tomato crate; cheese crate; flour-sack crate; empty crate.

Row 4: walnut service counter with cream marble top; vintage sage cash register; glass display with two pizzas; sink counter.

### dining.png

Row 1: gingham-clothed square table; round flower-vase bistro table; walnut chair southeast; matching chair southwest.

Row 2: brass cream floor lamp; leafy potted plant; olive tree; pendant lamp.

Row 3: A-frame chalk menu; red-and-cream striped awning; narrow open walnut doorway; arched blue-glass window.

Row 4: red upholstered booth; oval red patterned isometric rug; exterior lantern; green trash bin.

### items.png

Row 1: dough ball; flat sauced pizza base; cheese pizza; pepperoni pizza.

Row 2: closed plain pizza box; open empty box; stack of three boxes; plated pizza slice.

Row 3: long-handled pizza peel; sauce pot and ladle; flour sack with wheat emblem; basil pot.

Row 4: salt and pepper shakers; red squeeze bottle; round clock reading 10:10; walnut chalk menu board with decorative marks.

## Shared character prompt

Create a production animation spritesheet of ONE original cozy pixel-art game character. Canvas portrait 1024x1536, EXACTLY 4 columns by 6 rows, 24 fully separate complete body poses, generous transparent margins inside each invisible cell. REAL alpha transparency, no shadows, no floor, no labels, no lines. Every frame same character, same scale, identical clothes and proportions, feet on same baseline relative to each cell. Crisp chunky pixels, warm upper-left light, dark plum outlines, cream/sage/terracotta palette matching a cozy isometric pizzeria. 2:1 isometric RPG 3/4 viewpoint, roughly 42 pixels tall when exported at native game size. Columns are successive animation keyframes, distinct limb positions with true stepping not just translations. Row 1: front southeast idle breathing two frames, then front southeast talking gesture two frames. Row 2: southeast walk four-frame gait (left contact, passing, right contact, passing). Row 3: northeast BACK view walk four-frame gait, face NOT visible. Row 4: southeast working four frames with arms moving. Row 5: southeast carrying object with four-frame walking gait. Row 6: southeast frustrated gesturing four frames (hands hips, arms raised, pointing, sigh). Do not add symbols, furniture, speech bubbles or other people. All 24 entire figures fully inside cells.

Character suffixes:

- `chef.png`: friendly stocky male chef, brown moustache, cream tall toque and double-breasted jacket, brick-red neckerchief, sage apron, dark pants, brown shoes; kneads dough and carries pizza on a tray.
- `cashier.png`: medium-brown-skinned woman, curly dark bun, sage shirt, terracotta waist apron, navy trousers, cream sneakers; writes orders and carries a pizza box.
- `stock_worker.png`: dark-brown-skinned worker, mustard beanie, terracotta shirt, navy dungarees, brown boots; checks clipboard and carries a tomato crate.
- `customer_amber.png`: light-skinned young woman, auburn shoulder-length hair, mustard sweater, dark jeans, burgundy shoes; taps phone and carries pizza box.
- `customer_teal.png`: medium-brown-skinned older man, cropped grey hair, moustache, round glasses, teal cardigan, cream shirt, tan trousers, dark loafers; reads paper menu and carries pizza box.
- `customer_lavender.png`: brown-skinned young adult, short black hair, red cap, dusty lavender hoodie, charcoal jeans, white sneakers; taps phone and carries pizza box.

## Export processing

The reproducible builder thresholds source alpha at 160, drops tiny disconnected specks, extracts each transparent cell, samples to native pixel dimensions, and pads to registered canvases. Character scaling stays fixed within each character; bottom foot regions determine horizontal registration. Opposite directions use horizontal mirrors. The exported sprites have binary alpha; source sheets retain their original alpha. Animation durations and frame mappings are authored in the builder. The geometry tiles and UI are constructed directly at their final native pixel resolution.

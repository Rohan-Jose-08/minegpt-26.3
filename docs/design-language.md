# The design language (Phase 18)

MineGPT can design a building of its own in a few lines instead of placing blocks one by one. The planner writes the design; the brain turns it into blocks, turns every stair, door, bed and chest the right way, lists the materials, and refuses a design that cannot be built with the line and the reason.

Tools for the planner: `design_guide` (the language, the parts, a style card and examples near a size and style), `check_design` (errors, floor plan, materials, a review of what to fix so it looks good), then `build_structure(kind="design", program=..., x, y, z)`. `plan_structure` and `inspect_build` take `kind="design"` too. A design that was built and passed the check is kept in `designs.json` in the data folder and offered again.

You can try one without the game:

```
cd brain
python -c "from minegpt_brain.agent import design; c = design.compile_design(open('my.design').read()); print(design.floor_plan(c)); print(design.review(c)); print(c.structure.materials())"
```

## The language

```
The design language. One statement per line (or several split with ;). Sizes are in blocks.
  build <name> style=<style or theme>          optional first line
  mass <name> box(width, wall height, depth)   the first mass; width 4-21 (x), height 3-9, depth 4-21 (z)
  mass <name> box(w,h,d) attach <mass>.<side> [align=back|center|front or west|center|east] [offset=n] [floors=2]
                                               shares that wall; a doorway between the two is cut for you
  mirror <mass> as <name>                      a twin of an attached mass on the opposite side
  roof <mass> gable(overhang=1, ridge=x|z) | hip(overhang=1) | flat(parapet=1) | lean(to=<side>)
  facade <mass>.<side> -> [part, part, ...]    one part per bay, west to east or north to south
                                               parts: wall, window, tall_window, wide_window, door, door_porch, opening, buttress
  detail <mass> chimney [at <side>]
  detail <mass> furnish [bed, crafting_table, furnace, chest]     [] for none
Sides are north, east, south, west; the front is south unless facing turns the whole design.
Left to the compiler: every coordinate, which way stairs, doors, beds and chests are turned, posts at the corners and between bays, a window in every bay of a wall with no facade, a door if you write none, the floor, the stone footing, torches inside, the ladder to an upper floor.
Parts:
  wall (facade bay): Plain wall. Needs: nothing. Costs: nothing extra.
  window (facade bay): One glass pane at eye level, two high under a tall ceiling. Needs: a bay at least 1 wide. Costs: 1 or 2 window.
  tall_window (facade bay): A pane two high. Needs: walls 4 high or more. Costs: 2 window.
  wide_window (facade bay): Panes across the bay, up to three wide. Also written window_bay. Needs: a bay 2 or 3 wide reads best. Costs: 2 to 6 window.
  door (facade bay): A door with a torch on each side outside. Needs: open ground in front; nothing attached there. Costs: 1 door, 2 light.
  door_porch (facade bay): A door under a small roof on two fence posts, with torches. Needs: three clear blocks in front of the wall. Costs: 1 door, 4 fence, 6 ridge (slab), 2 light.
  opening (facade bay): A doorway with no door, for a barn or a shed. Needs: open ground in front. Costs: nothing extra.
  buttress (facade bay): A short stone pier against the wall, capped with a stair. For stone walls. Needs: open ground in front. Costs: 2 foundation, 1 roof (stair).
  chimney (detail <mass> chimney [at <side>]): A chimney up the outside of a wall to over the roof. Needs: a free wall: best a gable end. Costs: wall height plus roof height, in chimney blocks.
  furnish (detail <mass> furnish [items]): Furniture along the walls: bed, crafting_table, furnace, chest, barrel and the like. The first mass gets bed, crafting_table, furnace, chest unless told otherwise; [] for none. Needs: free wall inside. Costs: the items; a bed is 3 wool and 3 planks.
  gable (roof): Two slopes and a ridge. gable(overhang=1, ridge=x|z): ridge=x runs it east-west. By default along the longer side. Needs: any mass. Costs: about 1.5 stairs per block of floor, plus the core.
  hip (roof): Slopes on all four sides. hip(overhang=1). Needs: a mass at least 5 by 5. Costs: a little more than a gable.
  flat (roof): A flat deck. flat(parapet=1) adds a low rim of slabs. Needs: any mass. Costs: one roof_fill per block of floor.
  lean (roof): One slope, rising towards a side: lean(to=east). On an attached mass it rises towards the mass it is attached to. Needs: a span of 6 or less across the slope. Costs: about 1 stair per block of floor, plus the core.
```

## Examples

### cottage_with_wing

L shape: a hall with a lower wing set back, a porch and a chimney. 15x10, 495 blocks. Suits: cottage, oak, birch_lodge, birch, cherry_house, cherry.

```
build cottage_with_wing
mass hall box(9,4,7)
mass wing box(5,3,5) attach hall.west align=back
roof hall gable(overhang=1) ; roof wing gable(overhang=1)
facade hall.south -> [window, door_porch, window]
detail hall chimney at east
```

Ground floor (north at the top):

```
  P#o#P#o#P#o#P
  #...#...ccc.o
  o..._......b#
  #i.iP.......PC
  P#o#P.......o
      #i.....i#
      Po#PD#PoP
         i+i
         +++
```

### small_cabin

one low hall with a lean-to shed on a short side. 14x9, 403 blocks. Suits: cabin, spruce, cottage, oak, savanna_hut, acacia.

```
build small_cabin
mass hall box(9,4,6)
mass shed box(4,3,5) attach hall.east align=back
roof hall gable(overhang=1) ; roof shed lean(to=west)
facade hall.south -> [window, door_porch, window]
facade shed.south -> [opening]
detail hall chimney at west
detail shed furnish [chest, chest, barrel]
```

Ground floor (north at the top):

```
  P#o#P#o#Po#P
  o...cc.c#cc#
  P.....b._.co
 CP.......P.i#
  oi.....iP_#P
  Po#PD#PoP
     i+i
     +++
```

### first_house

the smallest house that is not a box: a 7x6 hall with a porch and a hip roof. 9x9, 223 blocks. Suits: cottage, oak, spruce, birch, jungle_lodge, jungle, swamp_house, mangrove.

```
build first_house
mass hall box(7,3,6)
roof hall hip(overhang=1)
facade hall.south -> [window, door_porch, window]
detail hall chimney at north
```

Ground floor (north at the top):

```
     C
  Po#Po#P
  o.ccc.o
  P....bP
  P.....P
  oi...io
  PoPDPoP
    i i
    +++
```

### t_house

T shape: a long hall with a gabled wing coming forward from the middle, the door in the wing. 13x13, 489 blocks. Suits: cottage, oak, dark_oak, manor, cherry_house.

```
build t_house
mass hall box(11,4,6)
mass front box(5,3,5) attach hall.south align=center
roof hall gable(overhang=1) ; roof front gable(overhang=1, ridge=z)
facade front.south -> [door_porch]
facade hall.north -> [window, window, window]
detail hall chimney at east
```

Ground floor (north at the top):

```
  Po#P#o#Po#P
  o.....ccc.o
  P........bP
  P.........PC
  oi.......io
  Po#P#_#Po#P
     #...#
     o...o
     #i.i#
     P#D#P
      i+i
      +++
```

### manor

a tall two-floor hall with two matching wings, symmetric about the door. 23x12, 1109 blocks. Suits: manor, dark_oak, stone_brick, cobblestone.

```
build manor
mass hall box(11,7,9) floors=2
mass west_wing box(6,4,7) attach hall.west align=center
mirror west_wing as east_wing
roof hall hip(overhang=1) ; roof west_wing gable(overhang=1, ridge=x) ; roof east_wing gable(overhang=1, ridge=x)
facade hall.south -> [tall_window, door_porch, tall_window]
facade hall.north -> [window, wall, window]
detail hall chimney at north
detail west_wing furnish [chest, chest, chest, chest]
detail east_wing furnish [furnace, furnace, crafting_table]
```

Ground floor (north at the top):

```
            C
       Po#P###Po#P
  PoPPoPi...ccc.HPoPPoP
  occcc#.......b.#.ccco
  #....#.........#....#
  P...._........._....P
  o....#.........#....o
  #i..i#.........#i..i#
  PoPPoPi.......iPoPPoP
       Po#P#D#Po#P
           i+i
           +++
```

### desert_court

flat-roofed blocks of different heights pushed together, with parapets. 13x11, 455 blocks. Suits: desert_house, sandstone.

```
build desert_court style=desert_house
mass hall box(9,5,7)
mass side box(5,4,5) attach hall.east align=front
mass store box(5,3,4) attach hall.north align=west
roof hall flat(parapet=1) ; roof side flat(parapet=1) ; roof store flat
facade hall.south -> [window, door, window]
facade side.east -> [window]
detail store furnish [chest, chest]
```

Ground floor (north at the top):

```
  P#o#P
  o.cco
  #i.i#
  P#_#P#o#P
  o...ccc.o
  #......bP#o#P
  P.......P...#
  o......._...o
  #i.....i#i.i#
  Po#PD#PoP#o#P
     i i
```

### keep

a square stone hall with a taller two-floor tower on a corner, buttresses on the long wall. 14x11, 652 blocks. Suits: keep, stone_brick, cobblestone.

```
build keep style=keep
mass hall box(9,5,9)
mass tower box(5,9,5) attach hall.east align=back floors=2
roof hall hip(overhang=1) ; roof tower flat(parapet=1)
facade hall.south -> [tall_window, door, tall_window]
facade hall.west -> [buttress, tall_window, buttress]
facade tower.east -> [tall_window]
detail hall chimney at north
```

Ground floor (north at the top):

```
      C
  P#o#P#o#P#o#P
 C#i..cc.c#..H#
  #.....b._...o
  P.......#i.i#
  o.......P#o#P
  #.......#
  P.......o
 C#i.....i#
  Po#PD#PoP
     i i
```

### barn

a tall barn with an open doorway and a low lean-to along one side, for storage and animals. 12x11, 504 blocks. Suits: cottage, oak, spruce, cabin, savanna_hut, acacia.

```
build barn
mass barn box(7,5,9)
mass stall box(4,3,7) attach barn.west align=center
roof barn gable(overhang=1, ridge=z) ; roof stall lean(to=east)
facade barn.south -> [opening]
facade barn.north -> [wide_window]
facade stall.south -> [opening]
detail barn furnish [chest, chest, chest, chest, barrel, barrel]
```

Ground floor (north at the top):

```
     P#ooo#P
  Po#Pccccc#
  o..#c....o
  #..#i....#
  P.._.....P
  o..#.....#
  #.i#.....o
  P_#Pi...i#
     P##_##P
```

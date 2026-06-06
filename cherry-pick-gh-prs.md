I have the following PRs pending to be pulled upstream:

* Add `.gitignore` (#51)
* Fix `_get_important_outputs` (#53)
* Bzlmod  (#54)
* Fix spelling  (#55)
* Update `local_container_links` to use `python==3.13` (#56)
* Add L4 support  (#58)
* Upgrade `sqlalchemy` (#61)

I want to make a script that does the following:
1) Takes in an (ordered) list of github PRs or local branches, as well as a local "to-cherry-pick-to" branch.
2) For each PR/branch, checks if the changes to their base are disjoint.
3) If they aren't disjoint, should error.
4) If they are disjoint, stacks the PRs/branches on top of the "to-cherry-pick-to" branch. Each PR/branch should be squashed into a single commit. The commit name should be as if github squash+rebased it to the target branch (I believe something like "Title (#123)", e.g. "Upgrade sqlalchemy (#61)")
5) Report result to command line.


Are there existing tools that could help with this? Maybe e.g. copybara? Is it easy to cook something like this manually?

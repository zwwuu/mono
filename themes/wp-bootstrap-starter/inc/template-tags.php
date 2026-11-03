<?php
/**
 * Template tags: post meta, Bootstrap pagination, comment markup, menu fallback.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

if ( ! function_exists( 'wbs_posted_on' ) ) :
	/**
	 * Print the post date and author.
	 */
	function wbs_posted_on() {
		printf(
			/* translators: 1: post date, 2: author name. */
			esc_html__( 'Posted on %1$s by %2$s', WBS_TEXTDOMAIN ),
			esc_html( get_the_date() ),
			'<a href="' . esc_url( get_author_posts_url( get_the_author_meta( 'ID' ) ) ) . '">' . esc_html( get_the_author() ) . '</a>'
		);
	}
endif;

if ( ! function_exists( 'wbs_posted_in' ) ) :
	/**
	 * Print categories and tags for the current post.
	 */
	function wbs_posted_in() {
		$cats = get_the_category_list( esc_html__( ', ', WBS_TEXTDOMAIN ) );
		$tags = get_the_tag_list( '', esc_html__( ', ', WBS_TEXTDOMAIN ) );

		if ( $cats ) {
			printf(
				'<span class="cat-links d-block mb-1">%s %s</span>',
				esc_html__( 'Posted in', WBS_TEXTDOMAIN ),
				$cats // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- core-generated links.
			);
		}
		if ( $tags ) {
			printf(
				'<span class="tags-links d-block">%s %s</span>',
				esc_html__( 'Tagged', WBS_TEXTDOMAIN ),
				$tags // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- core-generated links.
			);
		}
	}
endif;

if ( ! function_exists( 'wbs_pagination' ) ) :
	/**
	 * Bootstrap-styled numbered pagination for archive loops.
	 */
	function wbs_pagination() {
		$links = paginate_links(
			array(
				'type'      => 'array',
				'prev_text' => __( '&laquo; Previous', WBS_TEXTDOMAIN ),
				'next_text' => __( 'Next &raquo;', WBS_TEXTDOMAIN ),
			)
		);
		if ( ! $links ) {
			return;
		}

		echo '<nav aria-label="' . esc_attr__( 'Posts navigation', WBS_TEXTDOMAIN ) . '"><ul class="pagination justify-content-center">';
		foreach ( $links as $link ) {
			$active = strpos( $link, 'current' ) !== false;
			echo '<li class="page-item' . ( $active ? ' active' : '' ) . '">'
				. str_replace( 'page-numbers', 'page-link', $link ) // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped
				. '</li>';
		}
		echo '</ul></nav>';
	}
endif;

if ( ! function_exists( 'wbs_comment' ) ) :
	/**
	 * Bootstrap card markup for a single comment (wp_list_comments callback).
	 *
	 * @param WP_Comment $comment Comment object.
	 * @param array      $args    Display arguments.
	 * @param int        $depth   Comment depth.
	 */
	function wbs_comment( $comment, $args, $depth ) {
		$tag = ( 'div' === $args['style'] ) ? 'div' : 'li';
		?>
		<<?php echo esc_attr( $tag ); ?> id="comment-<?php comment_ID(); ?>" <?php comment_class( 'card mb-3' . ( $comment->comment_parent ? ' ms-4' : '' ), $comment ); ?>>
			<div class="card-body">
				<div class="d-flex align-items-center mb-2">
					<?php echo get_avatar( $comment, 48 ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
					<div>
						<strong><?php comment_author_link( $comment ); ?></strong><br>
						<small class="text-muted">
							<?php
							printf(
								/* translators: 1: comment date, 2: comment time. */
								esc_html__( '%1$s at %2$s', WBS_TEXTDOMAIN ),
								esc_html( get_comment_date( '', $comment ) ),
								esc_html( get_comment_time() )
							);
							?>
						</small>
					</div>
				</div>

				<?php if ( '0' === $comment->comment_approved ) : ?>
					<p class="alert alert-info"><?php esc_html_e( 'Your comment is awaiting moderation.', WBS_TEXTDOMAIN ); ?></p>
				<?php endif; ?>

				<?php comment_text( $comment ); ?>

				<div class="mt-2 small">
					<?php
					comment_reply_link(
						array_merge(
							$args,
							array(
								'depth'      => $depth,
								'max_depth'  => $args['max_depth'],
								'add_below'  => 'comment',
								'reply_text' => __( 'Reply', WBS_TEXTDOMAIN ),
							)
						)
					);
					edit_comment_link( __( 'Edit', WBS_TEXTDOMAIN ), ' <span class="ms-2">', '</span>' );
					?>
				</div>
			</div>
		<?php
	}
endif;

if ( ! function_exists( 'wbs_menu_fallback' ) ) :
	/**
	 * Simple page-based fallback when no menu is assigned to the primary location.
	 */
	function wbs_menu_fallback() {
		$pages = get_pages( array( 'sort_column' => 'menu_order, post_title' ) );
		if ( ! $pages ) {
			return;
		}
		echo '<ul class="navbar-nav ms-auto mb-2 mb-lg-0">';
		foreach ( $pages as $page ) {
			printf(
				'<li class="nav-item"><a class="nav-link" href="%s">%s</a></li>',
				esc_url( get_permalink( $page ) ),
				esc_html( $page->post_title )
			);
		}
		echo '</ul>';
	}
endif;
